import pytest
from pydantic import ValidationError

from alfred_server.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ALFRED_ENV", "ALFRED_LOG_LEVEL", "ALFRED_HOST", "ALFRED_PORT"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings(_env_file=None)

    assert settings.env == "dev"
    assert settings.log_level == "INFO"
    assert settings.port == 8000


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ALFRED_PORT", "9000")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"
    assert settings.port == 9000


def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
