import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 下面三个 import 依赖 alembic.ini 里的 prepend_sys_path = %(here)s，
# 它把 backend/ 放进 sys.path，否则这里 import app.* 会失败。
#
# app.db.models 必须导入。这些表是通过 import 的副作用注册到 Base.metadata 上的，
# 不导入则元数据为空，autogenerate 会把库里已有的表全当成多余的，
# 生成一堆 DROP TABLE。
import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# disable_existing_loggers=False 是必须的：fileConfig 这个参数默认是 True，
# 会把 alembic.ini 里没提到的 logger 全部 disabled=True。进程内调用迁移时
# （tests/conftest.py 就是这么建测试库的），应用的 logger 会被一起弄哑——
# 表现是日志凭空消失，而不是报错，非常难查。实测：不传这个参数，
# 跑完 command.upgrade 后 app.core.errors 的 disabled 变成 True。
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 连接串默认只有一个来源：.env（经 app.core.config）。alembic.ini 里那份是占位符，
# 如果这一行没执行成功，报错信息里会直接出现 PLACEHOLDER-SET-BY-env.py，
# 一眼能看出问题在哪，而不是连到某个意外的库上。
#
# config.attributes 是 Alembic 官方的「调用方传参」通道：命令行不经过它，
# 但进程内调用（如 tests/conftest.py 用 command.upgrade 建测试库）可以塞一个
# sqlalchemy_url 进来覆盖。没有它的话，测试夹具没法把迁移指向 <库名>_test——
# settings 是 import 时就构造好的单例，事后再改环境变量也没用。
#
# replace("%", "%%") 不是多余的：set_main_option 底层是 ConfigParser，
# 而 % 在里面是插值符号。密码里出现 % 时不转义就会抛 InterpolationSyntaxError。
database_url = config.attributes.get("sqlalchemy_url") or settings.database_url
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 默认情况下 Alembic 只比较「列是否存在」，不比较类型。
        # 开着它，String(50) 改成 String(100) 这类改动才会被 autogenerate 发现，
        # 否则会静默漏掉，直到线上写入超长数据才报错。
        compare_type=True,
        # 同理，默认不比较服务端默认值。关掉的话，把 DEFAULT now() 改成别的
        # 表达式也会被漏掉。
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
