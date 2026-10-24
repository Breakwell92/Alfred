import os

import pytest
from sqlalchemy.exc import OperationalError

from alfred_server import db
from alfred_server.config import Settings

# Set to a reachable Postgres to run the smoke test (CI provides one; locally, the compose
# stack: see docs/infra.md). Not ALFRED_-prefixed, so the autouse fixture leaves it alone.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_connects_to_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL
    monkeypatch.setenv("ALFRED_DATABASE_URL", TEST_DATABASE_URL)
    settings = Settings(_env_file=None)

    version = db.check_database(settings.database_url.get_secret_value())

    assert version.startswith("PostgreSQL")


def test_unreachable_database_raises() -> None:
    # Port 1 on loopback refuses connections immediately.
    with pytest.raises(OperationalError):
        db.check_database("postgresql+psycopg://alfred@127.0.0.1:1/alfred", timeout_seconds=2)


def test_main_reports_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALFRED_DATABASE_URL", "postgresql+psycopg://alfred@127.0.0.1:1/alfred")

    assert db.main() == 1
    assert "database check failed" in capsys.readouterr().err


def test_main_prints_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def fake_check(url: str, timeout_seconds: int = 5) -> str:
        seen.append(url)
        return "PostgreSQL 18.6"

    monkeypatch.setattr(db, "check_database", fake_check)
    monkeypatch.setenv("ALFRED_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")

    assert db.main() == 0
    assert seen == ["postgresql+psycopg://u:p@db:5432/x"]
    assert capsys.readouterr().out.strip() == "PostgreSQL 18.6"
