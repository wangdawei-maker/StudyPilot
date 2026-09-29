"""注册、登录与会话的业务逻辑。

路由层只负责接参数、返响应（NFR-MAINT-02），规则都在这里。
"""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ErrorCode, InvalidCredentialsError
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    session_expires_at,
    verify_password,
)
from app.db.integrity import constraint_name_of
from app.db.models import AuthSession, User
from app.services.workspaces import create_workspace_with_owner


class EmailAlreadyRegisteredError(ConflictError):
    """FR-AUTH-03：同一邮箱重复注册必须被拒绝，且错误码稳定。"""

    code = ErrorCode.AUTH_EMAIL_TAKEN
    message = "该邮箱已被注册"


# 从模型上现取约束名，不写死字符串：命名规范一改，写死的那份不会报错，
# 只会让下面的判断永远为 False，静默退化成 500。取不到就直接在 import 时炸掉。
_USERS_EMAIL_UNIQUE_CONSTRAINT = next(
    constraint.name
    for constraint in User.__table__.constraints
    if isinstance(constraint, UniqueConstraint)
    and [column.name for column in constraint.columns] == ["email"]
)


async def _email_taken(session: AsyncSession, email: str) -> bool:
    return await session.scalar(select(User.id).where(User.email == email).limit(1)) is not None


def _is_email_unique_violation(exc: IntegrityError) -> bool:
    """判断这个完整性错误是不是 users.email 的唯一冲突。

    必须按约束名精确判断，不能「见到 IntegrityError 就当成邮箱重复」：
    那样会把别的完整性问题（例如 email 小写 CHECK 被绕过）也报成「邮箱已注册」，
    真正的原因被掩盖掉，排查时会被带到完全错误的方向。

    判断逻辑在 app/db/integrity.py：asyncpg 的异常挂在 `exc.orig.__cause__` 上，
    不在 `exc.orig` 上。那段解释只写在该模块里，避免两处各讲一半。
    """
    return constraint_name_of(exc) == _USERS_EMAIL_UNIQUE_CONSTRAINT


def _default_workspace_name(user: User) -> str:
    """个人空间的默认名字。

    优先用显示名；没填就用中性的固定名，**不放邮箱**。邮箱是个人信息，
    而空间名以后可能被邀请进来的成员看到（FR-WS-05），不该把邮箱片段写进去。
    """
    return f"{user.display_name} 的学习空间" if user.display_name else "我的学习空间"


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """注册用户，并为他建好个人学习空间（FR-AUTH-01~03、FR-AUTH-08）。

    先查一次邮箱是否占用，是为了让常见情况走一条干净的错误路径——
    不必先让数据库报错、再回滚。

    但预检查挡不住竞态：两个请求可能同时查到「邮箱没被占用」。所以下面还兜了
    唯一约束。缺了兜底的话，并发下重名注册会变成 500，而 FR-AUTH-03 要的是
    一个稳定的错误码。
    """
    if await _email_taken(session, email):
        raise EmailAlreadyRegisteredError()

    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )

    try:
        session.add(user)
        # 先单独 flush 一次，而不是让它和建空间一起提交：
        # 一是拿 user.id（见 create_workspace_with_owner 的说明），
        # 二是让邮箱唯一冲突在**这里**暴露——否则它会推迟到建空间时的那次 flush，
        # 报错被记在建空间头上，排查方向就歪了。
        await session.flush()

        # FR-AUTH-08：注册即建个人空间，并把用户写为 owner。
        # 和上面的建用户同属一个事务（这里不 commit），要么都成要么都不成。
        await create_workspace_with_owner(
            session, name=_default_workspace_name(user), owner_id=user.id
        )

        await session.commit()
    except IntegrityError as exc:
        # 事务已经失败，必须回滚才能继续用这条会话，否则后续语句都会报
        # "current transaction is aborted"。
        await session.rollback()
        if _is_email_unique_violation(exc):
            raise EmailAlreadyRegisteredError() from exc
        raise

    return user


async def login_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
) -> tuple[User, str, datetime]:
    """校验邮箱密码并开一个会话（FR-AUTH-04~06）。

    返回 `(用户, 令牌原文, 到期时刻)`。令牌原文**只在这一次返回里存在**，
    路由层必须当场写进 Cookie；库里存的是它的摘要（FR-AUTH-10）。
    """
    user = await session.scalar(select(User).where(User.email == email))

    # 这一行是 FR-AUTH-04 的落点，写法比看上去讲究：
    # 即使 user 是 None 也要真的跑一遍 Argon2（verify_password 内部会拿一个假哈希陪跑）。
    # 写成 `if user is None or not verify_password(...)` 就短路了——「邮箱不存在」的
    # 响应会比「密码错」快几十毫秒，这个时间差足够拿来逐个试出哪些邮箱注册过。
    password_ok = verify_password(password, user.password_hash if user else None)
    if user is None or not password_ok:
        # 两种情况抛出的是同一个异常类、同一个 code、同一段文案，里外完全一致。
        raise InvalidCredentialsError()

    token = generate_session_token()
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=session_expires_at(),
    )
    session.add(auth_session)
    await session.commit()

    return user, token, auth_session.expires_at


async def resolve_session_user(session: AsyncSession, token: str | None) -> User | None:
    """拿令牌原文换出用户；任何一环不成立都返回 None。

    四种「不成立」在这里被压成同一个结果：没带令牌、令牌不认识、令牌对应的会话
    已过期（FR-AUTH-06）、会话行已被登出删掉。翻译成什么 HTTP 状态是路由层的事，
    服务层不关心——这样它也不依赖 Web 框架。

    查的是 token_hash 而不是令牌原文：库里根本没有原文（FR-AUTH-10）。

    顺带说明为什么不过期会话在这里顺手删掉：删行要提交事务，而这条路径是
    每个受保护请求都要走的读路径，为了清一行数据去做一次写，代价和收益不成比例。
    清理留给按 expires_at 扫的定时任务（那列上已经建了索引）。
    """
    if not token:
        return None

    row = (
        await session.execute(
            # 一次 join 同时取用户和到期时刻，省掉「先查会话再查用户」的第二个来回。
            select(User, AuthSession.expires_at)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == hash_session_token(token))
        )
    ).first()
    if row is None:
        return None

    user, expires_at = row
    # expires_at 是 timestamptz，取出来就是带时区的；拿 naive 的 datetime.now()
    # 比会直接抛 TypeError，所以必须给 UTC。
    if expires_at <= datetime.now(UTC):
        return None

    return user


async def logout_user(session: AsyncSession, token: str | None) -> None:
    """删掉当前会话，令牌立即失效（FR-AUTH-07）。

    **按 token_hash 删，不能按 user_id 删。** 后者会把该用户在所有设备上的会话
    一起干掉，而 FR-AUTH-07 只要求退出当前这一个（全端下线是 FR-AUTH-11 改密码
    才需要的行为）。

    没带令牌、或令牌本来就无效时什么都不做，也不报错：调用方要的结果是
    「我现在处于未登录状态」，这个结果已经成立了。为此返回 401 只会让前端在
    会话过期后点退出时弹一个用户无法理解的错误。
    """
    if not token:
        return

    await session.execute(
        delete(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
    )
    await session.commit()
