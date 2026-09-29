"""应用配置。

配置从仓库根目录的 .env 读取。`.env` 之所以放在仓库根目录而不是 backend/ 下，
是因为 docker-compose.yml 也在根目录，compose 的变量替换只认它旁边的 .env。
后端从 backend/ 启动，所以下面显式解析到根目录，而不是依赖当前工作目录。
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

# app/core/config.py -> app/core -> app -> backend -> 仓库根目录
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://studypilot:change-this-local-password@localhost:5432/studypilot"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # .env 里还有 docker-compose 用的变量（POSTGRES_DB、COMPOSE_PROJECT_NAME 等），
        # 它们不属于应用配置，忽略即可，不要让它们导致启动失败。
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_secret: str = "replace-with-a-long-random-development-secret"
    database_url: str = DEFAULT_DATABASE_URL

    # .env 里写成逗号分隔的形式（CORS_ORIGINS=a,b）。pydantic-settings 默认
    # 会把 list 类型的字段按 JSON 解析，直接写 a,b 会因为不是合法 JSON 而报错，
    # 所以用 NoDecode 关掉自动解析，交给下面的校验器切分。
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # FR-AUTH-06：会话有效期默认 7 天。
    session_ttl_days: int = 7

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def test_database_url(self) -> str:
        """测试库连接串 = 开发库名加 `_test` 后缀。

        不单独配一个 TEST_DATABASE_URL 环境变量，是有意的：那样它会和
        DATABASE_URL 各自漂移（改了主机忘了改测试库），而且多一个必须同步维护的地方。
        派生出来则天然与开发库同主机、同账号、同后缀，只需要维护一份。
        """
        url = make_url(self.database_url)
        if not url.database:
            raise ValueError("DATABASE_URL 里没有库名，无法派生测试库")
        return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


settings = Settings()
