"""数据库约束的回归测试（DR-01 ~ DR-08）。

这些约束第 2 天用一次性 SQL 手工验过，验完就没了。数据库层的约束有个特点：
**写错了不报错，只是不生效**。外键少一个 ondelete，唯一索引漏一个 WHERE，
都不会有任何提示，要等到很后面才以「权限判定莫名其妙不对」「删用户删不干净」
这种面目出现。所以它们必须变成每次跑测试都会过一遍的东西。

两条贯穿本文件的写法：

1. **正向对照和反向对照都要有。** 只验「非法输入被拒绝」的话，一条把所有输入
   都拒之门外的约束也能全绿。凡是「应当被拒绝」的用例，附近都有一条「合法输入
   应当通过」的用例。
2. **断言具体是哪条约束挂的，而不是「反正报错了」。** 比如大写邮箱那条，
   它因为唯一索引失败的话用例照样绿，而真正要守的 CHECK 其实已经没了。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrity import NOT_NULL_VIOLATION, constraint_name_of, raw_db_error
from app.db.models import AuthSession, User, Workspace, WorkspaceMember

# 不是真哈希，只是为了让 NOT NULL 的 password_hash 有个值。
# 本文件验的是表结构，不碰密码逻辑。（FR-AUTH-02 管的是接口和日志里不能出现
# 明文/哈希，这是条测试用的常量，不是真实用户的密码。）
FAKE_HASH = "$argon2id$test-only-not-a-real-hash"

# 与 security.session_expires_at 无关，写死是为了让本文件不依赖业务代码。
SESSION_TTL_FOR_TESTS = timedelta(days=7)


# --- 小工具 -----------------------------------------------------------------


async def _count(session: AsyncSession, model: type[Any]) -> int:
    """数库里的行数。

    必须走 SQL 而不是查 ORM 的 identity map：对象还在会话里不代表行还在表里，
    级联删除尤其如此——数据库把行删了，而 SQLAlchemy 手里的对象毫不知情。
    """
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def _make_user(session: AsyncSession, email: str = "alice@example.com") -> User:
    user = User(email=email, password_hash=FAKE_HASH)
    session.add(user)
    await session.flush()
    return user


async def _make_workspace(
    session: AsyncSession, owner: User, name: str = "学习空间"
) -> Workspace:
    workspace = Workspace(name=name, owner_id=owner.id)
    session.add(workspace)
    await session.flush()
    return workspace


def _make_session_row(
    user: User, token_hash: str = "f" * 64
) -> AuthSession:
    return AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + SESSION_TTL_FOR_TESTS,
    )


async def _rejects(
    session: AsyncSession, constraint: str, *objects: Any
) -> IntegrityError:
    """断言这些对象写入时被**指定的那条**约束挡下。

    用 `begin_nested` 而不是 `session.rollback()`，是为了只回滚这一次失败的写入。
    直接 rollback 会把本用例里先前那些成功的写入一起抹掉，于是「先建用户、
    再验证某个约束、最后回头断言用户还在」这种写法会莫名其妙地失败。
    """
    with pytest.raises(IntegrityError) as excinfo:
        async with session.begin_nested():
            session.add_all(list(objects))
            await session.flush()

    actual = constraint_name_of(excinfo.value)
    assert actual == constraint, f"期望被 {constraint} 挡下，实际违反的是 {actual}"
    return excinfo.value


async def _rejects_not_null(session: AsyncSession, column: str, *objects: Any) -> None:
    """NOT NULL 违反走的是另一条路：没有 constraint_name，列名在 column_name 上。"""
    with pytest.raises(IntegrityError) as excinfo:
        async with session.begin_nested():
            session.add_all(list(objects))
            await session.flush()

    error = raw_db_error(excinfo.value)
    assert error.sqlstate == NOT_NULL_VIOLATION, f"sqlstate={error.sqlstate}"
    assert error.column_name == column


# --- users ------------------------------------------------------------------


@pytest.mark.parametrize(
    "email", ["Alice@example.com", "ALICE@EXAMPLE.COM", "alice@Example.com"]
)
async def test_email_must_be_stored_lowercase(
    db_session: AsyncSession, email: str
) -> None:
    """ck_users_email_lowercase。

    Postgres 的字符串比较区分大小写，不归一化的话 Alice@x.com 和 alice@x.com
    会变成两个账号——而用户认为它们是同一个，后果是「注册时说邮箱已被占用，
    但不记得密码的那个账号我登不上去」。
    """
    await _rejects(
        db_session, "ck_users_email_lowercase", User(email=email, password_hash=FAKE_HASH)
    )


async def test_lowercase_email_is_accepted(db_session: AsyncSession) -> None:
    """正向对照。少了这条，一条「所有邮箱都拒绝」的 CHECK 也能让上面全绿。"""
    user = await _make_user(db_session, email="alice@example.com")

    assert user.email == "alice@example.com"


async def test_duplicate_email_is_rejected(db_session: AsyncSession) -> None:
    """uq_users_email（FR-AUTH-03 的数据库层兜底）。"""
    await _make_user(db_session, email="alice@example.com")

    await _rejects(
        db_session,
        "uq_users_email",
        User(email="alice@example.com", password_hash=FAKE_HASH),
    )


async def test_uppercase_variant_is_blocked_by_the_check_not_the_unique_index(
    db_session: AsyncSession,
) -> None:
    """「一个邮箱只有一个账号」是两件事凑出来的，这条把分工钉清楚。

    uq_users_email 是**区分大小写**的逐字节比较，它挡不住 Alice@x.com。
    真正挡住它的是 ck_users_email_lowercase——大写变体压根存不进去，
    于是唯一索引要比较的永远只有小写形式。

    所以这里断言的是 CHECK 而不是唯一索引：哪天有人把归一化挪到应用层、
    顺手删了这条 CHECK，唯一索引看起来还在，但邮箱归一化实际上已经失守了。
    """
    await _make_user(db_session, email="alice@example.com")

    await _rejects(
        db_session,
        "ck_users_email_lowercase",
        User(email="ALICE@EXAMPLE.COM", password_hash=FAKE_HASH),
    )


# --- workspaces -------------------------------------------------------------


async def test_workspace_owner_must_exist(db_session: AsyncSession) -> None:
    """fk_workspaces_owner_id_users。"""
    await _rejects(
        db_session,
        "fk_workspaces_owner_id_users",
        Workspace(name="孤儿空间", owner_id=uuid.uuid4()),
    )


async def test_workspace_owner_id_is_required(db_session: AsyncSession) -> None:
    """owner_id 不能为空。

    不是形式主义：它和 DR-06 是同一件事的两面。级联删除需要一条从 workspaces
    直达 users 的外键，而外键允许 NULL 的话，一个 owner_id 为空的空间在「主人」
    被删之后会永久留下——谁也删不掉它，因为没有任何一条规则指向它。
    """
    await _rejects_not_null(db_session, "owner_id", Workspace(name="无主空间", owner_id=None))


async def test_workspace_owner_must_be_a_real_user(db_session: AsyncSession) -> None:
    """正向对照：owner_id 指向真实用户时一切正常。"""
    owner = await _make_user(db_session)
    workspace = await _make_workspace(db_session, owner)

    assert workspace.owner_id == owner.id


# --- workspace_members.role（DR-03）-----------------------------------------


@pytest.mark.parametrize("role", ["admin", "OWNER", "Owner", " guest", ""])
async def test_role_must_be_owner_or_member(
    db_session: AsyncSession, role: str
) -> None:
    """ck_workspace_members_role_valid。

    "OWNER" / "Owner" 也在里面，是因为 CHECK 的比较区分大小写，而权限判定走的是
    `role == "owner"`（app/services/workspaces.py）。一个存进去的 "Owner" 在权限
    判定里会被当成普通成员——**静默降权**，空间主人发现自己的空间动不了了。
    """
    user = await _make_user(db_session)
    workspace = await _make_workspace(db_session, user)

    await _rejects(
        db_session,
        "ck_workspace_members_role_valid",
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role),
    )


@pytest.mark.parametrize("role", ["owner", "member"])
async def test_both_valid_roles_are_accepted(
    db_session: AsyncSession, role: str
) -> None:
    """正向对照：取值 SQL 是从 WORKSPACE_ROLES 常量生成的，别把常量本身改坏了。"""
    user = await _make_user(db_session)
    workspace = await _make_workspace(db_session, user)
    member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
    db_session.add(member)

    await db_session.flush()

    assert member.role == role


# --- workspace_members 的键与索引 --------------------------------------------


async def test_the_same_user_cannot_join_one_workspace_twice(
    db_session: AsyncSession,
) -> None:
    """pk_workspace_members 是 (workspace_id, user_id) 复合主键，不是自增 id。

    允许重复行的话，权限判定会变成「按行取 role」，取到哪一行取决于返回顺序——
    同一份数据能得出两种权限结论。
    """
    owner = await _make_user(db_session)
    workspace = await _make_workspace(db_session, owner)
    member = await _make_user(db_session, email="bob@example.com")
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="member")
    )
    await db_session.flush()

    await _rejects(
        db_session,
        "pk_workspace_members",
        WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="member"),
    )


async def test_a_workspace_cannot_have_two_owners(db_session: AsyncSession) -> None:
    """uq_workspace_members_single_owner（部分唯一索引）。

    这是「owner 身份在库里存了两份」这个已知冗余的兜底之一：owner_id 和
    role='owner' 的行理论上会不一致，数据库挡不住这种不一致，但至少不能出现
    **两个** owner 行。
    """
    owner = await _make_user(db_session)
    workspace = await _make_workspace(db_session, owner)
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner")
    )
    await db_session.flush()

    second = await _make_user(db_session, email="bob@example.com")
    await _rejects(
        db_session,
        "uq_workspace_members_single_owner",
        WorkspaceMember(workspace_id=workspace.id, user_id=second.id, role="owner"),
    )


async def test_one_owner_plus_many_members_is_allowed(db_session: AsyncSession) -> None:
    """正向对照之一：唯一性是**部分**的（WHERE role='owner'），不能误伤普通成员。

    去掉 postgresql_where 的话，一个空间就只能有一个人——而「邀请成员」
    （FR-WS-05）正是这个项目的核心功能之一。
    """
    owner = await _make_user(db_session)
    workspace = await _make_workspace(db_session, owner)
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner")
    )
    for index in range(3):
        member = await _make_user(db_session, email=f"member{index}@example.com")
        db_session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="member")
        )

    await db_session.flush()

    assert await _count(db_session, WorkspaceMember) == 4


async def test_each_workspace_can_have_its_own_owner(db_session: AsyncSession) -> None:
    """正向对照之二：唯一性按 workspace_id 分组，不是全局唯一。

    索引是建在 workspace_id 上的，一个人可以有任意多个「自己当主人的空间」。
    写成对 user_id 唯一的话，注册流程会在用户创建第二个空间时直接炸。
    """
    user = await _make_user(db_session)
    first = await _make_workspace(db_session, user, name="空间一")
    second = await _make_workspace(db_session, user, name="空间二")
    db_session.add_all(
        [
            WorkspaceMember(workspace_id=first.id, user_id=user.id, role="owner"),
            WorkspaceMember(workspace_id=second.id, user_id=user.id, role="owner"),
        ]
    )

    await db_session.flush()

    assert await _count(db_session, WorkspaceMember) == 2


# --- auth_sessions ----------------------------------------------------------


async def test_token_hash_must_be_unique(db_session: AsyncSession) -> None:
    """uq_auth_sessions_token_hash（FR-AUTH-10 的配套约束）。

    两个会话撞上同一个摘要意味着令牌生成出了问题。这时必须当场失败——
    放过去的话，后一个会话会指向**前一个用户的**会话行，那是越权。
    """
    user = await _make_user(db_session)
    db_session.add(_make_session_row(user, token_hash="a" * 64))
    await db_session.flush()

    await _rejects(
        db_session, "uq_auth_sessions_token_hash", _make_session_row(user, "a" * 64)
    )


async def test_different_token_hashes_and_users_are_fine(
    db_session: AsyncSession,
) -> None:
    """正向对照：同一用户可以有多个会话（多设备），FR-AUTH-12 是 P2 的现状。"""
    user = await _make_user(db_session)
    db_session.add_all(
        [_make_session_row(user, "a" * 64), _make_session_row(user, "b" * 64)]
    )

    await db_session.flush()

    assert await _count(db_session, AuthSession) == 2


# --- 级联删除（DR-06）-------------------------------------------------------


async def test_deleting_a_user_cascades_to_their_workspaces(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    await _make_workspace(db_session, user)

    await db_session.delete(user)
    await db_session.flush()

    assert await _count(db_session, Workspace) == 0


async def test_deleting_a_user_cascades_to_their_memberships(
    db_session: AsyncSession,
) -> None:
    """他作为**成员**（不是主人）的成员行也要消失。

    这一条走的是 fk_workspace_members_user_id_users，和上面那条走的是不同的外键。
    两个外键都漏配 ondelete 的话，删用户时会留下指向不存在用户的成员行，
    而权限判定只看 workspace_members——一个已注销的人还能被算成空间成员。
    """
    owner = await _make_user(db_session)
    workspace = await _make_workspace(db_session, owner)
    member = await _make_user(db_session, email="bob@example.com")
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="member")
    )
    await db_session.flush()

    await db_session.delete(member)
    await db_session.flush()

    assert await _count(db_session, WorkspaceMember) == 0
    # 空间本身是别人的，不能被连坐删掉。
    assert await _count(db_session, Workspace) == 1


async def test_deleting_a_workspace_cascades_to_its_members(
    db_session: AsyncSession,
) -> None:
    """走 fk_workspace_members_workspace_id_workspaces。"""
    owner = await _make_user(db_session)
    workspace = await _make_workspace(db_session, owner)
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner")
    )
    await db_session.flush()

    await db_session.delete(workspace)
    await db_session.flush()

    assert await _count(db_session, WorkspaceMember) == 0
    # 用户是独立的实体，删空间不该动他。
    assert await _count(db_session, User) == 1


async def test_deleting_a_user_cascades_to_their_sessions(
    db_session: AsyncSession,
) -> None:
    """走 fk_auth_sessions_user_id_users。

    漏配的话，被删用户的会话行会留下来。那个令牌仍然能被认证路径查到，
    于是「已注销的账号还能继续访问」。
    """
    user = await _make_user(db_session)
    db_session.add(_make_session_row(user))
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    assert await _count(db_session, AuthSession) == 0


async def test_dr06_deleting_a_user_removes_everything_that_hangs_off_them(
    db_session: AsyncSession,
) -> None:
    """DR-06 的验收，也是本文件的收口。

    前面几条把「哪个外键」定位清楚了，这一条验的是需求本身。两个用户各有一整套
    数据，只删其中一个——**另一个必须原封不动**。这道对照是关键：级联写宽了
    （比如外键指错表）在单用户场景下完全看不出来，只有并排放两套数据才会暴露。
    """
    leaving = await _make_user(db_session, email="leaving@example.com")
    leaving_workspace = await _make_workspace(db_session, leaving, name="要消失的空间")
    db_session.add(
        WorkspaceMember(
            workspace_id=leaving_workspace.id, user_id=leaving.id, role="owner"
        )
    )
    db_session.add(_make_session_row(leaving, "a" * 64))

    staying = await _make_user(db_session, email="staying@example.com")
    staying_workspace = await _make_workspace(db_session, staying, name="要留下的空间")
    db_session.add(
        WorkspaceMember(
            workspace_id=staying_workspace.id, user_id=staying.id, role="owner"
        )
    )
    db_session.add(_make_session_row(staying, "b" * 64))
    await db_session.flush()

    await db_session.delete(leaving)
    await db_session.flush()

    remaining_workspaces = (
        await db_session.scalars(select(Workspace))
    ).all()
    assert [workspace.id for workspace in remaining_workspaces] == [staying_workspace.id]

    remaining_members = (await db_session.scalars(select(WorkspaceMember))).all()
    assert [row.user_id for row in remaining_members] == [staying.id]

    remaining_sessions = (await db_session.scalars(select(AuthSession))).all()
    assert [row.user_id for row in remaining_sessions] == [staying.id]
