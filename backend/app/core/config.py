"""应用配置。

配置从仓库根目录的 .env 读取。`.env` 之所以放在仓库根目录而不是 backend/ 下，
是因为 docker-compose.yml 也在根目录，compose 的变量替换只认它旁边的 .env。
后端从 backend/ 启动，所以下面显式解析到根目录，而不是依赖当前工作目录。
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
