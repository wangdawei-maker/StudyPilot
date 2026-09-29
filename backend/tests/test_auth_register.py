"""注册接口（FR-AUTH-01~03、FR-AUTH-08）。"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.db.models import User, Workspace, WorkspaceMember
from app.services import auth as auth_service

REGISTER_URL = "/api/v1/auth/register"
PASSWORD = "correct horse battery"


def _payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"email": "alice@example.com", "password": PASSWORD}
    data.update(overrides)
    return data


async def test_register_returns_the_created_user(client: AsyncClient) -> None:
    response = await client.post(REGISTER_URL, json=_payload(display_name="Alice"))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "display_name", "created_at"}
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "Alice"
    # created_at 由数据库的 now() 生成。它要是没被取回来，这里会是 null，
    # 或者在异步下访问时直接抛 MissingGreenlet。
    assert body["created_at"]


async def test_response_never_contains_password_or_hash(client: AsyncClient) -> None:
    """FR-AUTH-02：接口响应里既不能有明文密码，也不能有哈希。"""
    response = await client.post(REGISTER_URL, json=_payload())

    assert PASSWORD not in response.text
    assert "$argon2" not in response.text


async def test_password_is_stored_hashed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.post(REGISTER_URL, json=_payload())

    user = await db_session.scalar(select(User).where(User.email == "alice@example.com"))
    assert user is not None
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    # 存进去的哈希必须真的能验出原密码，否则就是存了个没法登录的东西。
    assert security.verify_password(PASSWORD, user.password_hash)


async def test_email_is_normalized_to_lowercase(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """不归一化的话，一个邮箱能注册出两个账号，而用户认为是同一个。"""
    response = await client.post(REGISTER_URL, json=_payload(email="Alice@Example.COM"))

    assert response.status_code == 201
    assert response.json()["email"] == "alice@example.com"

    user = await db_session.scalar(select(User).where(User.email == "alice@example.com"))
    assert user is not None


async def test_duplicate_email_is_rejected_with_a_stable_code(
    client: AsyncClient,
) -> None:
    """FR-AUTH-03。前端靠 code 判断，不靠中文文案（API-03）。"""
    await client.post(REGISTER_URL, json=_payload())

    response = await client.post(REGISTER_URL, json=_payload())

    assert response.status_code == 409
    assert response.json()["code"] == "AUTH_EMAIL_TAKEN"


async def test_duplicate_email_differs_only_by_case(client: AsyncClient) -> None:
    """大小写不同也是同一个账号，否则归一化就白做了。"""
    await client.post(REGISTER_URL, json=_payload(email="alice@example.com"))

    response = await client.post(REGISTER_URL, json=_payload(email="ALICE@EXAMPLE.COM"))

    assert response.status_code == 409
    assert response.json()["code"] == "AUTH_EMAIL_TAKEN"


async def test_duplicate_email_race_is_reported_as_conflict(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """竞态兜底：两个请求同时查到「邮箱没被占用」时，靠唯一约束收场。

    把这个分支骗出来：预检查一律说「没被占用」，于是第二次注册一定会撞上
    数据库的唯一约束。不兜底的话这里会变成 500，而 FR-AUTH-03 要的是稳定错误码。
    """
    await client.post(REGISTER_URL, json=_payload())

    async def _pretend_email_is_free(session: AsyncSession, email: str) -> bool:
        return False

    monkeypatch.setattr(auth_service, "_email_taken", _pretend_email_is_free)

    response = await client.post(REGISTER_URL, json=_payload())

    assert response.status_code == 409
    assert response.json()["code"] == "AUTH_EMAIL_TAKEN"


async def test_password_shorter_than_eight_is_rejected(client: AsyncClient) -> None:
    """FR-AUTH-01。顺带守住 FR-AUTH-02：报错时不能把密码回显出来。"""
    # 6 位，稳稳低于 8 位下限。（写成 8 位的标记会变成「合法的密码」，
    # 用例就静默失效了——第一版就是这么写错的。）
    short = "LEAKME"
    response = await client.post(REGISTER_URL, json=_payload(password=short))

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert short not in response.text


async def test_password_of_exactly_eight_is_accepted(client: AsyncClient) -> None:
    """边界：FR-AUTH-01 说的是「不少于 8 位」，8 位本身是合法的。"""
    response = await client.post(REGISTER_URL, json=_payload(password="12345678"))

    assert response.status_code == 201


async def test_password_longer_than_the_cap_is_rejected(client: AsyncClient) -> None:
    """上限是为了防止有人拿超大字符串来烧 CPU 做哈希。"""
    response = await client.post(REGISTER_URL, json=_payload(password="x" * 129))

    assert response.status_code == 422


async def test_malformed_email_is_rejected(client: AsyncClient) -> None:
    response = await client.post(REGISTER_URL, json=_payload(email="not-an-email"))

    assert response.status_code == 422


async def test_display_name_is_optional(client: AsyncClient) -> None:
    response = await client.post(REGISTER_URL, json=_payload())

    assert response.status_code == 201
    assert response.json()["display_name"] is None


async def test_blank_display_name_becomes_null(client: AsyncClient) -> None:
    """前端清空输入框提交的是空串，不该在库里留一个区分不出「没设置」的空白值。"""
    response = await client.post(REGISTER_URL, json=_payload(display_name="   "))

    assert response.status_code == 201
    assert response.json()["display_name"] is None


async def test_overlong_display_name_is_rejected(client: AsyncClient) -> None:
    """users.display_name 是 String(50)，超长要在这一层挡住，别让数据库报错。"""
    response = await client.post(REGISTER_URL, json=_payload(display_name="a" * 51))

    assert response.status_code == 422


async def test_registration_creates_a_personal_workspace_with_the_user_as_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """FR-AUTH-08，也是本步最容易写错的地方。

    「谁是空间主人」在库里存了两份：workspaces.owner_id 和 workspace_members 里
    role='owner' 的行。只写一份不会有任何报错，只是权限判定（FR-WS-03/04）
    或级联删除（DR-06）会在很后面才莫名其妙地失效。所以两处都要断言。
    """
    response = await client.post(REGISTER_URL, json=_payload(display_name="Alice"))
    user_id = response.json()["id"]

    workspace = await db_session.scalar(
        select(Workspace).where(Workspace.owner_id == user_id)
    )
    assert workspace is not None, "注册后没有建个人空间"
    assert workspace.name == "Alice 的学习空间"

    members = (
        await db_session.scalars(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
        )
    ).all()
    assert len(members) == 1, "个人空间应当恰好有一个成员"
    assert members[0].role == "owner"
    assert members[0].user_id == workspace.owner_id, (
        "workspaces.owner_id 和成员行里的 owner 指向了不同的人"
    )


async def test_default_workspace_name_falls_back_without_display_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """没填显示名时用中性名字，不拿邮箱凑——空间名以后可能被别的成员看到。"""
    response = await client.post(REGISTER_URL, json=_payload())

    workspace = await db_session.scalar(
        select(Workspace).where(Workspace.owner_id == response.json()["id"])
    )
    assert workspace is not None
    assert workspace.name == "我的学习空间"
    assert "alice" not in workspace.name


async def test_failed_registration_leaves_nothing_behind(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """重复注册失败后，不能留下半个用户或半个空间。

    建用户和建空间在同一个事务里，这里验证失败路径确实整体回滚了。
    """
    await client.post(REGISTER_URL, json=_payload())
    await client.post(REGISTER_URL, json=_payload())

    users = (await db_session.scalars(select(User))).all()
    workspaces = (await db_session.scalars(select(Workspace))).all()
    assert len(users) == 1
    assert len(workspaces) == 1
