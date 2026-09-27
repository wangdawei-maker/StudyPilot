"""数据表定义。

对应需求文档的 DR-01 ~ DR-08。第一周只建身份与学习空间这四张表，
文档、分块、任务等表留到第二周再加迁移。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# FR-AUTH-08 要求注册后立刻建个人空间，所以角色只有 owner / member 两种（DR-03）。
WORKSPACE_ROLES = ("owner", "member")

# 校验约束的 SQL 从上面的常量生成，避免取值在代码里写两份、日后各改各的。
_ROLE_VALUES_SQL = ", ".join(f"'{role}'" for role in WORKSPACE_ROLES)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # 254 是 RFC 5321 对邮箱地址长度的上限。
    # 唯一约束建在这列上，配合下面的 email_lowercase 检查，保证大小写不同的
    # 同一邮箱不会被注册成两个账号（FR-AUTH-03）。
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)

    # 只存 Argon2id 哈希，永不存明文（FR-AUTH-02）。
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        # 邮箱统一以小写存储。Postgres 的字符串比较区分大小写，如果不做归一化，
        # Alice@x.com 和 alice@x.com 会变成两个账号——而用户认为是同一个。
        # 归一化写在这里而不是只在应用层：应用层哪天漏了一处，数据库会直接拒绝。
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    def __repr__(self) -> str:
        # 刻意不包含 password_hash：FR-AUTH-02 要求哈希也不能出现在日志里，
        # 而 repr 是最容易随异常堆栈一起被打出来的东西。
        return f"<User id={self.id} email={self.email!r}>"


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # owner_id 是必须的，不能只靠 workspace_members 推断归属：
    # DR-06 要求删除用户时级联删除其学习空间，这需要一条从 workspaces
    # 直达 users 的外键，否则数据库层无从知道该删哪些空间。
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<Workspace id={self.id} name={self.name!r}>"


class WorkspaceMember(TimestampMixin, Base):
    """空间成员。用 (workspace_id, user_id) 复合主键，不需要额外的 id 列。"""

    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # user_id 单独建索引：查「我加入了哪些空间」走的是 user_id，
    # 而复合主键的索引前缀是 workspace_id，帮不上忙。
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"role IN ({_ROLE_VALUES_SQL})",
            name="role_valid",
        ),
        # owner 同时存在于 workspaces.owner_id 和这里 role='owner' 的行，
        # 两者理论上会不一致。这个部分唯一索引至少把「一个空间出现两个 owner」
        # 挡在数据库层。注意它只约束这里的行数，owner_id 是否指向同一个人
        # 仍然要靠应用层在一个事务里写。
        Index(
            "uq_workspace_members_single_owner",
            "workspace_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceMember workspace_id={self.workspace_id} "
            f"user_id={self.user_id} role={self.role!r}>"
        )


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """登录会话。

    退出登录直接删行，而不是打撤销标记：FR-AUTH-07 要求旧令牌立即失效，
    FR-AUTH-11 要求改密码后吊销其它设备的会话，这两件事都是一条 DELETE。
    """

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 只存令牌的 SHA-256 十六进制摘要（FR-AUTH-10），原文仅在 Cookie 里出现一次。
    # 用 SHA-256 而不是 Argon2 是有意的：令牌本身是 32 字节随机数，熵足够高，
    # 不存在被爆破的风险，没必要在每次请求的认证路径上付慢哈希的代价。
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # 默认 7 天（FR-AUTH-06）。建索引是为了清理过期会话的定时任务能走索引扫描。
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        # 同样不打印 token_hash。
        return (
            f"<AuthSession id={self.id} user_id={self.user_id} "
            f"expires_at={self.expires_at}>"
        )
