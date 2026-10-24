"""Server configuration, loaded from environment variables (and an optional `.env`).

Every variable is prefixed with `ALFRED_`, e.g. `ALFRED_LOG_LEVEL=DEBUG`.
The `.env` file is read from the process working directory, i.e. `server/` with the
documented commands. Document new variables in `infra/.env.example`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALFRED_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Loopback by default: the server will hold OAuth tokens and home controls, so
    # listening on all interfaces (0.0.0.0) must be an explicit choice, e.g. in compose.
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Shared bearer token the Android app sends on every request except /health.
    # Unset means protected routes refuse all requests (503), never that they are open.
    api_token: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings. Use as a FastAPI dependency.

    Cached: tests that change env vars should build `Settings(...)` directly or call
    `get_settings.cache_clear()`.
    """
    return Settings()
