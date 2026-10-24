"""Server configuration, loaded from environment variables (and an optional `.env`).

Every variable is prefixed with `ALFRED_`, e.g. `ALFRED_LOG_LEVEL=DEBUG`.
The `.env` file is read from the process working directory, i.e. `server/` with the
documented commands. Document new variables in `infra/.env.example`.
"""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
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

    # SQLAlchemy URL (psycopg 3 driver) for the compose Postgres. Secret because it
    # normally embeds the password; read it with `.get_secret_value()`.
    database_url: SecretStr = SecretStr("postgresql+psycopg://alfred@127.0.0.1:5432/alfred")
    ollama_base_url: str = "http://127.0.0.1:11434"

    # LiteLLM model gateway (infra/litellm/). Every LLM call goes through it, by alias; see
    # alfred_server.models.gateway. The key is LITELLM_MASTER_KEY or a virtual key made with it.
    litellm_base_url: str = "http://127.0.0.1:4000"
    litellm_api_key: SecretStr | None = None
    # LiteLLM's own database, where it writes spend logs (read by the budget service). Unset
    # means `database_url` with the database name changed to `litellm`, as in compose.
    litellm_database_url: SecretStr | None = None

    # The user's IANA timezone. Budget months and days start at local midnight; agents' request
    # context and the get_time tool use it too.
    timezone: str = "Europe/London"
    # Budget service (alfred_server.budget, docs/decisions/0008-budget-service.md). LiteLLM
    # prices in USD; the cap and spend are shown in GBP at this fixed rate. Err high.
    budget_usd_to_gbp: float = Field(default=0.80, gt=0)

    # Agno agent layer (docs/decisions/0007-agent-runtime.md).
    # OpenTelemetry traces of agent runs, model calls and tool calls, stored in Postgres (the
    # `agno` schema). Off by default so tests never touch a database.
    agent_tracing: bool = False

    # Connectors (alfred_server.connectors, docs/connectors.md).
    # Fernet key encrypting connector credentials and OAuth tokens in Postgres. Unset means no
    # connector that needs a secret can be set up or used (fail closed). Never change it once
    # set: stored secrets become unreadable. Generate one with
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    vault_key: SecretStr | None = None
    # Base URL the browser reaches this server at, for OAuth redirect URIs
    # (`<public_url>/connectors/<id>/auth/callback`). Unset uses the request's own URL.
    public_url: str | None = None
    # How often the background task renews OAuth tokens that expire within 5 minutes.
    connector_refresh_interval_s: float = Field(default=60.0, gt=0)

    # Speech-to-text (faster-whisper). See docs/decisions/0002-stt.md for the model choice.
    # Off by default so tests and CI never load a model; the voice gateway enables it.
    stt_enabled: bool = False
    stt_model: str = "distil-large-v3"
    stt_device: Literal["auto", "cuda", "cpu"] = "auto"
    # CTranslate2 compute type. "auto" = int8_float16 on CUDA (half the VRAM of float16 at the
    # same speed on the 3090) and int8 on CPU. Any CTranslate2 type name is also accepted.
    stt_compute_type: str = "auto"
    # Whisper language code. It has no en-GB variant; nudge spelling with stt_initial_prompt.
    stt_language: str = "en"
    stt_initial_prompt: str | None = None
    stt_beam_size: int = Field(default=1, ge=1)
    # Concurrent transcriptions sharing the one loaded model (1–2 keeps latency predictable).
    stt_max_concurrency: int = Field(default=1, ge=1, le=2)
    # Longest utterance accepted, in seconds; longer streams are cut off with an error.
    stt_max_audio_s: float = Field(default=30.0, gt=0)
    # Where models are downloaded; None uses the Hugging Face cache.
    stt_download_root: str | None = None

    # Text-to-speech (Kokoro, Piper fallback). See docs/decisions/0006-tts.md.
    # Off by default so tests and CI never load a model; the voice gateway enables it.
    tts_enabled: bool = False
    # Directory holding the model files (scripts/download_tts_models.py fills it).
    # None uses ~/.cache/alfred/tts.
    tts_model_dir: str | None = None
    # Engine of the default voice (Dan picked Piper's en_GB-alan-medium after a listening test).
    # Kokoro still loads for other voices; Piper is the automatic fallback if it won't.
    tts_engine: Literal["kokoro", "piper"] = "piper"
    tts_kokoro_voice: str = "bm_george"
    tts_kokoro_model: str = "kokoro-v1.0.onnx"
    tts_kokoro_voices: str = "voices-v1.0.bin"
    # "auto" uses CUDA when onnxruntime can, else CPU; "cuda" fails (and falls back to Piper)
    # rather than run Kokoro on the CPU.
    tts_kokoro_device: Literal["auto", "cuda", "cpu"] = "auto"
    tts_piper_voice: str = "en_GB-alan-medium"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings. Use as a FastAPI dependency.

    Cached: tests that change env vars should build `Settings(...)` directly or call
    `get_settings.cache_clear()`.
    """
    return Settings()
