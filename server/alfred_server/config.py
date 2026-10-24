"""Server configuration, loaded from environment variables (and an optional `.env`).

Every variable is prefixed with `ALFRED_`, e.g. `ALFRED_LOG_LEVEL=DEBUG`.
Document new variables in `infra/.env.example`.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALFRED_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings. Use as a FastAPI dependency."""
    return Settings()
