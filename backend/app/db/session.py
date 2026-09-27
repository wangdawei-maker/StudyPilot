"""异步数据库引擎与会话工厂。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# create_async_engine 是惰性的，这里不会真的去建立连接。
# 所以 Postgres 没启动时，导入本模块依然能成功——真正连不上会在
# 第一次执行查询时才发现。这一点在跑测试和写迁移脚本时很关键。
engine = create_async_engine(
    settings.database_url,
    # 连接被中间层悄悄掐断时（docker 重启、空闲超时、网络抖动），
    # 连接池里会留下已经死掉的连接，下次取出来用才报错。
    # pre_ping 在取用前先探一下，代价是一次极轻的往返。
    pool_pre_ping=True,
)

# expire_on_commit=False：默认情况下 commit 会让实例上所有属性过期，
# 之后再读任何字段都会触发一次懒加载查询。同步下这没问题，但在 async
# 上下文里，这种隐式 IO 是不允许的，会直接抛 MissingGreenlet。
# 关掉它，commit 之后对象仍然可读。
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：一个请求一个会话。

    这里只负责开和关，不回滚也不提交——事务边界由各个接口自己控制，
    免得出现「依赖默默 commit 了半截数据」这种情况。
    """
    async with SessionLocal() as session:
        yield session
