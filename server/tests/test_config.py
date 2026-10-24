import pytest
from pydantic import ValidationError

from alfred_server.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.log_level == "INFO"
    assert settings.api_token is None


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ALFRED_PORT", "9000")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"
    assert settings.port == 9000


def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_api_token_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_API_TOKEN", "test-token")

    settings = Settings(_env_file=None)

    assert settings.api_token is not None
    assert settings.api_token.get_secret_value() == "test-token"
    assert "test-token" not in repr(settings)
