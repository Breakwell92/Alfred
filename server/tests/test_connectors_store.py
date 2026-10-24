from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, select

from alfred_server.connectors.models import TokenSet
from alfred_server.connectors.store import (
    ConnectorStore,
    connector_credentials,
    connector_tokens,
)
from tests.budget_helpers import file_engine, memory_engine, postgres_engines  # noqa: F401
from tests.connector_helpers import make_service

EXPIRY = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)


def round_trip(make_engine: Callable[[], Engine]) -> None:
    first = make_engine()
    store = ConnectorStore(first)
    store.set_state("acme", enabled=True, status="connected")
    store.set_state("acme", enabled=True, status="needs_reauth", last_error="boom")  # upsert
    store.set_credentials("acme", "cipher-creds")
    store.set_tokens("acme", "cipher-tokens", EXPIRY)
    store.set_tokens("acme", "cipher-tokens-2", EXPIRY + timedelta(hours=1))
    store.add_oauth_state("s1", "acme", "cipher-verifier")
    first.dispose()

    second = make_engine()  # as after a restart
    try:
        store = ConnectorStore(second)
        state = store.state("acme")
        assert state is not None
        assert (state.enabled, state.status, state.last_error) == (True, "needs_reauth", "boom")
        assert store.credentials("acme") == "cipher-creds"
        tokens = store.tokens("acme")
        assert tokens is not None
        assert tokens.ciphertext == "cipher-tokens-2"
        assert tokens.expires_at == EXPIRY + timedelta(hours=1)
        assert store.tokens_expiring_before(EXPIRY + timedelta(hours=2)) == ["acme"]
        assert store.tokens_expiring_before(EXPIRY) == []

        pending = store.pop_oauth_state("s1")
        assert pending is not None and pending.connector_id == "acme"
        assert store.pop_oauth_state("s1") is None  # single use

        store.set_credentials("acme", None)
        store.delete_tokens("acme")
        assert store.credentials("acme") is None
        assert store.tokens("acme") is None
    finally:
        second.dispose()


def test_round_trip_sqlite(tmp_path: Path) -> None:
    round_trip(lambda: file_engine(tmp_path / "alfred.db"))


def test_round_trip_postgres(postgres_engines: Callable[[], Engine]) -> None:  # noqa: F811
    round_trip(postgres_engines)


def test_purge_old_oauth_states() -> None:
    store = ConnectorStore(memory_engine())
    store.add_oauth_state("old", "acme", "x")

    assert store.purge_oauth_states(datetime.now(UTC) - timedelta(minutes=10)) == 0
    assert store.purge_oauth_states(datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert store.pop_oauth_state("old") is None


def test_only_ciphertext_reaches_the_database() -> None:
    engine = memory_engine()
    service = make_service(engine=engine)
    service.update("keyed", enabled=True, api_key="sk-plain-key")
    service.update("acme", client_id="client-1", client_secret="client-secret-plain")
    service._save_tokens(
        "acme",
        TokenSet(access_token="access-plain", refresh_token="refresh-plain"),
    )

    with engine.connect() as conn:
        stored = [*conn.execute(select(connector_credentials)).all()]
        stored += [*conn.execute(select(connector_tokens)).all()]
    dump = repr(stored)
    for secret in ("sk-plain-key", "client-secret-plain", "access-plain", "refresh-plain"):
        assert secret not in dump
