"""测试夹具。

三条设计决定，都踩过坑：

1. **测试跑在真实的 Postgres 上，不用 SQLite。** 项目依赖的部分唯一索引
   （`uq_workspace_members_single_owner`）、`email = lower(email)` 这类 CHECK 约束、
   原生 `uuid` 类型，SQLite 一个都没有。用 SQLite 跑出来的绿色是假的。

2. **每个用例跑在自己的事务里，结束后整段回滚。** 用例之间因此互不污染，
   也不需要「先清表再插数据」这种脆弱的重置逻辑。要点是会话必须绑在 connection
   而不是 engine 上：接口内部会调 `session.commit()`，绑 engine 的话那个 commit
   提交的就是真实事务，夹具最后的 rollback 成了空操作——测试之间开始互相串味，
   而且是那种「单跑绿、全跑红」的隐蔽失效。
   （已实测：把 bind 改成 engine，test_b/test_c 立刻失败，且数据真的落进了测试库。）

3. **测试库名必须以 `_test` 结尾，且开工前先断言。** 这是防止哪天配置写错，
   夹具把迁移和回滚作用到开发库上。
"""

import asyncio
from collections.abc import AsyncGenerator, Callable

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import BACKEND_DIR, settings
from app.db.session import get_session
from app.main import app

TEST_DATABASE_URL = settings.test_database_url


async def _reset_test_database(database: str) -> None:
    """把测试库恢复成「存在且完全为空」：没有则建，有则清空 schema。

    不用 psycopg2：那需要另装一个同步驱动。asyncpg 本来就在依赖里，
    直接用 asyncio.run 起一次性的连接即可——连接用完就关，不会留下跨事件循环的句柄。

    注意 CREATE DATABASE 不能出现在事务里，asyncpg 的 execute 默认就是自动提交，
    所以这里不要手动 begin()。

    **为什么每轮都清空，而不是「已存在就沿用」**：迁移测试会真的降级再升级，
    一旦降级路径有问题，库里会留在「alembic_version 说在 base、表却还在」的
    自相矛盾状态。下一次跑测试时 upgrade 会撞上 `relation "users" already exists`，
    于是整个套件以一堆看不懂的错误开头，把真正的原因盖掉——排查方向直接跑偏。
    每次从零开始，这种跨轮次的状态污染就不存在了。代价只有一次迁移的时间
    （实测约 0.2 秒）。
    """
    dev = make_url(settings.database_url)
    admin = await asyncpg.connect(
        host=dev.host or "localhost",
        port=dev.port or 5432,
        user=dev.username,
        password=dev.password,
        # 连开发库而不是 postgres 维护库：开发库一定存在，维护库的名字各发行版不一样。
        database=dev.database,
    )
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", database
        )
        if not exists:
            await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    test = make_url(settings.test_database_url)
    connection = await asyncpg.connect(
        host=test.host or "localhost",
        port=test.port or 5432,
        user=test.username,
        password=test.password,
        database=test.database,
    )
    try:
        await connection.execute("DROP SCHEMA public CASCADE")
        await connection.execute("CREATE SCHEMA public")
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """测试库连接串。做成夹具而不是让用例 import 常量，是为了不用 import 别的测试模块。"""
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    """指向测试库的 alembic 配置。

    `config.attributes["sqlalchemy_url"]` 是 alembic 官方的「调用方传参」通道，
    backend/alembic/env.py 优先读它（细节见那里的注释）。没有这一步的话，
    迁移会作用到 .env 里的开发库上。
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["sqlalchemy_url"] = TEST_DATABASE_URL
    return config


@pytest.fixture(scope="session", autouse=True)
def prepared_test_database(alembic_config: Config, test_database_url: str) -> None:
    """整轮测试只做一次：清空测试库，把迁移跑到 head。

    必须是同步夹具。alembic 的命令内部走 asyncio.run()，
    在已经运行的事件循环里调用会直接抛 RuntimeError。
    """
    url = make_url(test_database_url)
    # 这道断言必须在任何写操作之前：下面两个函数都会真的删东西。
    if not url.database or not url.database.endswith("_test"):
        raise RuntimeError(
            f"测试库名必须以 _test 结尾，当前是 {url.database!r}。"
            "这道断言是防止夹具把回滚作用到开发库上。"
        )

    asyncio.run(_reset_test_database(url.database))
    command.upgrade(alembic_config, "head")


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """一个用例一个事务，用例结束整段回滚。

    engine 是按用例建的。它是惰性的，creation 本身不建连接，
    开销可以忽略；而做成 session 级反而危险——pytest-asyncio 默认每个用例
    一个新的事件循环，跨循环复用 asyncpg 连接会直接报错。
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                # 这行在当前代码里是冗余的，但值得留着：SQLAlchemy 的默认值
                # conditional_savepoint 只在「连接已经开着事务」时才用 SAVEPOINT，
                # 而上面恰好 begin() 过了，所以行为一样（实测去掉它测试仍全绿）。
                # 写死是为了不用依赖「上面那行 begin() 必须存在」这个隐含前提——
                # 哪天有人把 begin() 挪走，默认值会静默退化成 rollback_only，
                # commit 直接落库，测试才开始鬼打墙。
                join_transaction_mode="create_savepoint",
                # 与 app/db/session.py 保持一致，避免 commit 后访问属性触发额外查询。
                expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """调用接口的 HTTP 客户端，请求里用的就是 db_session 那条事务。"""

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=app)
        # base_url 用 https：会话 Cookie 会带 Secure 属性，httpx 只在 https 下回传它。
        # 想让这里用 http 的话，得让 Cookie 的 Secure 跟着 app_env 走，别硬编码。
        async with AsyncClient(transport=transport, base_url="https://testserver") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def new_client() -> AsyncGenerator[Callable[[], AsyncClient], None]:
    """再造一个独立的 HTTP 客户端，用完自动关掉。

    每个 AsyncClient 自带一个 Cookie jar，所以「登出只影响当前设备」「多个设备
    各自登录」这类用例必须要两个客户端才测得出来——同一个客户端里 Cookie 是共享的，
    第二次登录会直接覆盖第一次的，看起来像通过，实际什么都没验证。

    依赖覆盖挂在 app 对象上（不是挂在客户端上），所以这些客户端自动共享
    调用方正在用的那个 db_session。
    """
    created: list[AsyncClient] = []

    def _make() -> AsyncClient:
        http_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        )
        created.append(http_client)
        return http_client

    try:
        yield _make
    finally:
        for http_client in created:
            await http_client.aclose()
