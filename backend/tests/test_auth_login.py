"""登录接口与 Cookie 策略（FR-AUTH-04 ~ FR-AUTH-06、FR-AUTH-10）。"""

from http.cookies import SimpleCookie
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import SESSION_COOKIE_NAME
from app.core import security
from app.core.config import settings
from app.db.models import AuthSession
from app.services import auth as auth_service

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "correct horse battery"
EMAIL = "alice@example.com"


async def _register(client: AsyncClient, email: str = EMAIL) -> None:
    response = await client.post(
        REGISTER_URL,
        json={"email": email, "password": PASSWORD, "display_name": "Alice"},
    )
    assert response.status_code == 201, response.text


async def _login(client: AsyncClient, **overrides: Any) -> Response:
    payload: dict[str, Any] = {"email": EMAIL, "password": PASSWORD}
    payload.update(overrides)
    return await client.post(LOGIN_URL, json=payload)


def _cookie_of(response: Response) -> SimpleCookie:
    """把 Set-Cookie 解析出来。

    不能用 response.cookies：那是个只保留 name/value 的 CookieJar，HttpOnly、
    SameSite、Secure 这些属性全被丢掉了——而它们恰恰是本文件要断言的东西。
    """
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1, f"期望恰好一个 Set-Cookie，实际 {headers}"
    jar = SimpleCookie()
    jar.load(headers[0])
    return jar


def _cookie_flag(response: Response, flag: str) -> bool:
    """读 Cookie 上的布尔标志位（HttpOnly / Secure）。

    必须过一道 bool()：SimpleCookie 对**没出现过**的标志位返回空串而不是 False，
    于是 `morsel["secure"] is False` 会失败并报 `assert '' is False`——
    看着像实现漏了属性，其实是断言写法的问题，很容易被带偏。
    """
    return bool(_cookie_of(response)[SESSION_COOKIE_NAME][flag])


# --- 成功路径 ---------------------------------------------------------------


async def test_login_returns_the_logged_in_user(client: AsyncClient) -> None:
    await _register(client)

    response = await _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == EMAIL
    assert body["display_name"] == "Alice"
    assert set(body) == {"id", "email", "display_name", "created_at"}


async def test_login_sets_an_httponly_lax_cookie(client: AsyncClient) -> None:
    """FR-AUTH-05。这三个属性是令牌在浏览器里的全部保护，缺一个都是漏洞。"""
    await _register(client)

    response = await _login(client)
    cookie = _cookie_of(response)

    assert SESSION_COOKIE_NAME in cookie
    assert cookie[SESSION_COOKIE_NAME].value, "Cookie 值是空的，等于没登录"
    # HttpOnly：JS 读不到，XSS 偷不走令牌。
    assert _cookie_flag(response, "httponly") is True
    # SameSite=Lax：跨站发起的写请求不带 Cookie，这是本项目的 CSRF 防线。
    assert cookie[SESSION_COOKIE_NAME]["samesite"].lower() == "lax"
    assert cookie[SESSION_COOKIE_NAME]["path"] == "/"


async def test_cookie_is_secure_only_in_production(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-05 的「生产环境启用 Secure」。

    两个方向都要测。只测「生产环境有 Secure」的话，一个恒为 True 的实现也能过，
    而那种实现在本地 http 下会让 Cookie 根本发不出去，开发时莫名其妙登不上。
    """
    await _register(client)

    monkeypatch.setattr(settings, "app_env", "production")
    assert _cookie_flag(await _login(client), "secure") is True

    monkeypatch.setattr(settings, "app_env", "development")
    assert _cookie_flag(await _login(client), "secure") is False


async def test_cookie_max_age_matches_the_session_ttl(client: AsyncClient) -> None:
    """Cookie 寿命和库里会话的寿命必须一致（都来自 SESSION_TTL）。

    不一致的后果是错位的：Cookie 更长，用户会顶着一个服务端不认的令牌反复被 401；
    Cookie 更短，库里会攒下永远用不到的会话行。
    """
    await _register(client)

    morsel = _cookie_of(await _login(client))[SESSION_COOKIE_NAME]

    assert int(morsel["max-age"]) == security.SESSION_TTL_SECONDS
    assert security.SESSION_TTL_SECONDS == settings.session_ttl_days * 24 * 3600


async def test_login_email_is_case_insensitive(client: AsyncClient) -> None:
    """注册时归一化过，登录也得归一化，否则用户输对邮箱却登不进去。"""
    await _register(client, email="alice@example.com")

    response = await _login(client, email="ALICE@Example.COM")

    assert response.status_code == 200


# --- 失败路径：不得泄露账号是否存在（FR-AUTH-04）----------------------------


async def test_wrong_password_is_rejected(client: AsyncClient) -> None:
    await _register(client)

    response = await _login(client, password="wrong horse battery")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_INVALID_CREDENTIALS"


async def test_unknown_email_is_rejected(client: AsyncClient) -> None:
    await _register(client)

    response = await _login(client, email="nobody@example.com")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_INVALID_CREDENTIALS"


async def test_the_two_failure_modes_are_indistinguishable(client: AsyncClient) -> None:
    """FR-AUTH-04 的核心断言：把两条失败路径的响应逐字节比一遍。

    分别断言「都是 401」「code 相同」还不够——哪天有人在其中一条的 message 里
    补一句「该邮箱未注册」，分别断言仍然全绿，账号枚举的口子就开了。
    所以这里比的是除 request_id 外的整个响应体。
    """
    await _register(client)

    wrong_password = await _login(client, password="wrong horse battery")
    unknown_email = await _login(client, email="nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401

    def _body_without_request_id(response: Response) -> dict[str, Any]:
        body = response.json()
        body.pop("request_id")  # 每个请求本来就不同，不参与比较
        return body

    assert _body_without_request_id(wrong_password) == _body_without_request_id(
        unknown_email
    )


async def test_unknown_email_still_runs_the_hash_check(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-04 的时间侧信道：账号不存在时也必须真的跑一遍 Argon2。

    上一条比的是响应内容，内容一致但耗时差几十毫秒的话照样能枚举邮箱。
    这里直接盯住调用参数：账号不存在时传给 verify_password 的必须是 None
    （security.verify_password 见到 None 会拿假哈希陪跑）。

    写成 `if user is None or not verify_password(...)` 就会短路，
    这个用例会看到 seen == [] 而失败。
    """
    seen: list[str | None] = []
    real_verify = auth_service.verify_password

    def _spy(password: str, password_hash: str | None) -> bool:
        seen.append(password_hash)
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", _spy)

    await _login(client, email="nobody@example.com")

    assert seen == [None], "账号不存在时跳过了哈希校验，响应会明显更快"


# --- 令牌落库（FR-AUTH-10）--------------------------------------------------


async def test_token_is_stored_only_as_a_hash(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """FR-AUTH-10：库里不能出现令牌原文。"""
    await _register(client)
    token = _cookie_of(await _login(client))[SESSION_COOKIE_NAME].value

    sessions = (await db_session.scalars(select(AuthSession))).all()
    assert len(sessions) == 1

    stored = sessions[0].token_hash
    assert stored != token
    assert token not in stored
    # 存进去的摘要必须真的能对上原文，否则就是存了个永远查不到的东西。
    assert stored == security.hash_session_token(token)


async def test_each_login_opens_a_separate_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """同一账号登录两次开两个会话，互不覆盖。

    FR-AUTH-12（并发会话数上限）是 P2，第一版不限数量——这条用例把这个现状钉住，
    以后要做上限时它会提醒你：改动在这里。
    """
    await _register(client)

    first = _cookie_of(await _login(client))[SESSION_COOKIE_NAME].value
    second = _cookie_of(await _login(client))[SESSION_COOKIE_NAME].value

    assert first != second
    sessions = (await db_session.scalars(select(AuthSession))).all()
    assert len(sessions) == 2
    assert {row.token_hash for row in sessions} == {
        security.hash_session_token(first),
        security.hash_session_token(second),
    }


# --- 输入校验 ---------------------------------------------------------------


async def test_login_does_not_echo_the_password(client: AsyncClient) -> None:
    """FR-AUTH-02：失败响应里不能出现明文密码。"""
    await _register(client)
    secret = "wrong horse battery"

    response = await _login(client, password=secret)

    assert secret not in response.text


async def test_overlong_password_is_rejected(client: AsyncClient) -> None:
    """登录同样要过 Argon2，超长输入的 CPU 上限不能只堵注册那一半。"""
    await _register(client)

    response = await _login(client, password="x" * 129)

    assert response.status_code == 422


async def test_short_password_is_a_failed_login_not_a_format_error(
    client: AsyncClient,
) -> None:
    """登录不设密码下限：输了个短密码就是一次失败登录，不是格式错误。

    返回 422 的话，前端要为一个它无法处理的字段级错误做分支，而用户看到的
    提示也从「邮箱或密码不正确」变成了「密码太短」——后者把我们的规则暴露给了
    每一个来试密码的人，却对用户体验毫无帮助。
    """
    await _register(client)

    response = await _login(client, password="short")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_INVALID_CREDENTIALS"
