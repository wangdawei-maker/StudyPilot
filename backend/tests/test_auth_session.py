"""会话的读取、过期与登出（FR-AUTH-06 / FR-AUTH-07 / FR-AUTH-09）。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import SESSION_COOKIE_NAME
from app.db.models import AuthSession
from app.services import auth as auth_service


def _freeze_time(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    """把服务端看到的「现在」钉在 instant 上。

    替换的是 app/services/auth.py 里的模块级 datetime 名，不是标准库本身——
    只影响被测模块，别的代码照常拿真实时间。
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return instant

    monkeypatch.setattr(auth_service, "datetime", _FrozenDatetime)

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"
PASSWORD = "correct horse battery"
EMAIL = "alice@example.com"


async def _sign_in(client: AsyncClient, email: str = EMAIL) -> None:
    """注册并登录，让这个客户端带上会话 Cookie。"""
    registered = await client.post(
        REGISTER_URL, json={"email": email, "password": PASSWORD, "display_name": "Alice"}
    )
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text


def _raw_set_cookie(response: Response) -> str:
    """取 Set-Cookie 的原始字符串。

    删除 Cookie 那个响应里带着一个 RFC 1123 格式的过期时间，而 http.cookies 的
    SimpleCookie 对这种带逗号的日期解析并不稳，所以这一处直接看原始头。
    """
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1, f"期望恰好一个 Set-Cookie，实际 {headers}"
    return headers[0]


# --- 当前用户（FR-AUTH-09）--------------------------------------------------


async def test_me_returns_the_current_user(client: AsyncClient) -> None:
    await _sign_in(client)

    response = await client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


async def test_me_never_exposes_the_password_hash(client: AsyncClient) -> None:
    """FR-AUTH-02：受保护接口同样不能把哈希带出去。"""
    await _sign_in(client)

    response = await client.get(ME_URL)

    assert PASSWORD not in response.text
    assert "$argon2" not in response.text


async def test_me_without_a_cookie_is_unauthorized(client: AsyncClient) -> None:
    response = await client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


async def test_me_with_an_unknown_token_is_unauthorized(client: AsyncClient) -> None:
    """伪造的令牌不能蒙混过关——查的是摘要，猜不中就不认。

    注意这里连不上库：令牌先被 SHA-256 摘要，再拿去查唯一索引，
    所以「随便编一个」和「编一个长度对的」在服务端看来完全一样。
    """
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")

    response = await client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


# --- 过期（FR-AUTH-06）-----------------------------------------------------


async def test_expired_session_is_unauthorized(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """FR-AUTH-06：过期后受保护接口返回 401。

    直接把库里的 expires_at 改到过去，而不是等 7 天——过期判定必须由服务端
    看着库里的时间做，只看 Cookie 还在不在的话，一个被用户手动改过系统时间的
    浏览器就能拿到永久会话。
    """
    await _sign_in(client)
    assert (await client.get(ME_URL)).status_code == 200

    row = await db_session.scalar(select(AuthSession))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    response = await client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


async def test_expired_session_is_not_falsely_accepted_at_the_boundary(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """边界：判据是 expires_at <= now，所以「刚好到点」算过期。

    反过来写成 `<` 的话，过期瞬间的那个请求会被放行，两次请求之间就存在一个
    本该关闭却还开着的窗口。

    这里必须把服务端的「现在」冻住。不冻的话，写 expires_at = now 之后请求再过几毫秒
    才到，now 天然大于 expires_at，`<` 和 `<=` 都会判成过期——用例照样绿，
    却什么都没验证到。
    """
    await _sign_in(client)
    row = await db_session.scalar(select(AuthSession))
    assert row is not None

    boundary = datetime.now(UTC)
    _freeze_time(monkeypatch, boundary)

    row.expires_at = boundary
    await db_session.flush()
    assert (await client.get(ME_URL)).status_code == 401, "刚好到点应当算过期"

    # 再过一微秒就仍然有效。这个断言是上一句的对照：两句一起才能证明
    # 分界线的位置是对的，而不只是「什么东西返回了 401」。
    row.expires_at = boundary + timedelta(microseconds=1)
    await db_session.flush()
    assert (await client.get(ME_URL)).status_code == 200


# --- 登出（FR-AUTH-07）------------------------------------------------------


async def test_logout_invalidates_the_token_immediately(client: AsyncClient) -> None:
    """FR-AUTH-07 的核心：登出后旧令牌不能再用。

    退出后必须把 Cookie 换成一个新的客户端再来试，否则测的是「Cookie 还在不在」，
    而不是「服务端还认不认这个令牌」——前者靠清 Cookie 就能过，后者才是需求说的。
    """
    await _sign_in(client)
    token = client.cookies[SESSION_COOKIE_NAME]

    assert (await client.post(LOGOUT_URL)).status_code == 204

    client.cookies.set(SESSION_COOKIE_NAME, token)
    response = await client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


async def test_logout_deletes_the_session_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _sign_in(client)
    assert len((await db_session.scalars(select(AuthSession))).all()) == 1

    await client.post(LOGOUT_URL)

    assert (await db_session.scalars(select(AuthSession))).all() == []


async def test_logout_clears_the_cookie(client: AsyncClient) -> None:
    await _sign_in(client)

    header = _raw_set_cookie(await client.post(LOGOUT_URL))

    assert f'{SESSION_COOKIE_NAME}=""' in header
    assert "Max-Age=0" in header
    assert "HttpOnly" in header


async def test_logout_returns_no_body(client: AsyncClient) -> None:
    """204 按定义不能有响应体。用 JSONResponse 的话这里会是字面量 `null`。"""
    await _sign_in(client)

    response = await client.post(LOGOUT_URL)

    assert response.status_code == 204
    assert response.content == b""


async def test_logout_without_a_session_is_still_ok(client: AsyncClient) -> None:
    """幂等：会话已经过期后再点退出，不该给用户弹一个他无法理解的错误。

    （需求没写这条，是产品判断。要是你希望未登录调登出返回 401，
    改 app/services/auth.py 的 logout_user 抛 UnauthorizedError 即可。）
    """
    response = await client.post(LOGOUT_URL)

    assert response.status_code == 204


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    await _sign_in(client)

    assert (await client.post(LOGOUT_URL)).status_code == 204
    assert (await client.post(LOGOUT_URL)).status_code == 204


async def test_logout_only_affects_the_current_device(
    client: AsyncClient, new_client: Callable[[], AsyncClient]
) -> None:
    """在这台设备退出，不该把别的设备一起踢下线。

    如果 logout_user 按 user_id 删而不是按 token_hash 删，这条会失败——
    而按 user_id 删在单设备测试里是完全看不出来的，这就是这条用例存在的理由。
    （全端下线是 FR-AUTH-11 改密码时的行为，不是登出。）
    """
    other_device = new_client()
    await _sign_in(client)
    response = await other_device.post(
        LOGIN_URL, json={"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert (await other_device.get(ME_URL)).status_code == 200

    assert (await client.post(LOGOUT_URL)).status_code == 204

    assert (await client.get(ME_URL)).status_code == 401
    assert (await other_device.get(ME_URL)).status_code == 200, "另一台设备被误踢下线了"
