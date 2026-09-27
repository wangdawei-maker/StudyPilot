"""SQLAlchemy 声明式基类，以及各表共用的列定义。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 约束命名规范。必须在第一次生成迁移之前定下来：Postgres 会给没名字的约束
# 自动起名（users_email_key、users_pkey 这种），一旦库里已经存在这些名字，
# 之后再想换规范就得手工重命名。Alembic 的 autogenerate 也依赖它——
# 没有确定的名字，它生成不出可靠的 drop_constraint 语句，回滚就会失败。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    # 这里用 %(constraint_name)s，意味着每个 CheckConstraint 都必须显式命名，
    # 否则建表时直接抛错。这是故意留的：宁可报错，也不留下匿名约束。
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """UUID 主键。

    不用自增整数：这是 C 端产品，自增 id 会让 /api/workspaces/1、/2 这种
    能被顺序遍历，既泄露业务量也方便被爬。UUID 的生成开销可以忽略。

    id 由 Python 侧生成（default=uuid.uuid4），而不是交给数据库的
    gen_random_uuid()。这样 flush 之后马上就能拿到 id，不用为了拿 id
    多发一次查询，写迁移和测试时也不用管数据库版本。
    """

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """创建/更新时间，供各表混入。

    两列都是 timestamptz（带时区）。刻意不用不带时区的 timestamp：
    存进去的到底是什么时刻，会取决于服务器和会话的 TimeZone 设置，
    换个环境读出来就可能差几个小时，而且这种问题往往到线上才暴露。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
