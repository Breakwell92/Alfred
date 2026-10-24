import pytest
from pydantic import ValidationError

from alfred_server.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.log_level == "INFO"
    assert settings.api_token is None
    assert settings.database_url.get_secret_value() == (
        "postgresql+psycopg://alfred@127.0.0.1:5432/alfred"
    )
    assert settings.ollama_base_url == "http://127.0.0.1:11434"


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ALFRED_PORT", "9000")
    monkeypatch.setenv("ALFRED_DATABASE_URL", "postgresql+psycopg://u:secret@db:5432/x")
    monkeypatch.setenv("ALFRED_OLLAMA_BASE_URL", "http://100.64.0.1:11434")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"
    assert settings.port == 9000
    assert settings.database_url.get_secret_value() == "postgresql+psycopg://u:secret@db:5432/x"
    assert settings.ollama_base_url == "http://100.64.0.1:11434"


def test_database_url_is_not_shown_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_DATABASE_URL", "postgresql+psycopg://u:secret@db:5432/x")

    assert "secret" not in repr(Settings(_env_file=None))


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
