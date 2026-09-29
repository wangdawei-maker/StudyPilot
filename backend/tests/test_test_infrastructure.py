"""测试夹具自身的测试。

这一组用例不测业务，测的是 conftest.py 有没有真的做到隔离。它存在的理由：
夹具失效是**静默**的——回滚变成空操作时，单跑每个用例都绿，只有全量跑才红，
而且报错信息会指向别的地方。这里把它变成一条会当场失败的断言。

三个用例必须按文件顺序执行（pytest 默认就是），彼此之间存在依赖：
test_a 写入的数据，test_b 必须看不到。所以没有标独立，改动顺序会破坏这组断言。
"""

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.db.models import User
from app.db.session import get_session
from app.main import app

# 从 settings 现取，不从 conftest 转一手：conftest 会被 pytest 按自己的方式加载，
# 再 import 一次可能拿到另一个模块对象，没必要留这个悬念。
TEST_DATABASE_URL = settings.test_database_url


async def test_a_writes_a_user(db_session: AsyncSession) -> None:
    """写入并 commit。commit 是必须的：接口内部都会 commit，要模拟真实路径。"""
    db_session.add(
        User(email="probe@example.com", password_hash="not-a-real-hash", display_name="探针")
    )
    await db_session.commit()

    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_b_sees_an_empty_table(db_session: AsyncSession) -> None:
    """上一条用例写的数据不该留在这里——留了说明回滚没生效。"""
    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 0, "上个用例的数据没被回滚，用例之间已经串味了"


async def test_c_commit_does_not_escape_to_other_connections(
    db_session: AsyncSession,
) -> None:
    """最要命的一种失效：commit 穿透了外层事务，提交到了真实库里。

    前两条用例都在同一条连接上观察，即使 commit 真的落库了也看不出来。
    这里换一条独立的连接去看——只有真的没落库，才看得见 0。
    """
    db_session.add(User(email="escape@example.com", password_hash="not-a-real-hash"))
    await db_session.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as outsider:
            count = await outsider.scalar(select(func.count()).select_from(User))
    finally:
        await engine.dispose()

    assert count == 0, "接口里的 commit 提交到了真实数据库，join_transaction_mode 没起作用"


async def test_d_client_reaches_the_app_and_swaps_in_the_test_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """夹具链路走通：ASGITransport → app → 路由，且会话依赖被换成测试事务。

    现在还没有接口用 get_session，所以覆盖是否生效只能直接解析依赖来断言。
    等第 3 天有了真接口，接口自己的测试就会覆盖这条路径，这里可以删。
    """
    response = await client.get("/health")
    assert response.status_code == 200

    assert await anext(app.dependency_overrides[get_session]()) is db_session
