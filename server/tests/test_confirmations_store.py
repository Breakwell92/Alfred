from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from alfred_server.confirmations.models import PendingConfirmation
from alfred_server.confirmations.store import ConfirmationStore, NotificationRow
from tests.budget_helpers import file_engine, postgres_engines  # noqa: F401

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def confirmation(id: str = "c1", session_id: str = "s1") -> PendingConfirmation:
    return PendingConfirmation(
        id=id,
        run_id=f"run-{id}",
        session_id=session_id,
        specialist="general",
        alias="local-main",
        tools=["send_test_message"],
        summary="Send a test message to Sam: 'Hi'",
        tier="write",
        channel="text",
        device_type="text",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )


def round_trip(make_engine: Callable[[], Engine]) -> None:
    first = make_engine()
    store = ConfirmationStore(first)
    store.add(confirmation("c1"))
    store.add(confirmation("c2", session_id="s2"))
    store.set_override("test_messages", "send_test_message", "read")
    store.set_override("test_messages", "send_test_message", "irreversible")  # upsert
    store.set_override("time", "get_time", "write")
    store.set_override("time", "get_time", None)
    store.add_notification(NotificationRow("n1", "confirmation", "Title", "Body", "c1", NOW))
    first.dispose()

    second = make_engine()  # as after a restart
    try:
        store = ConfirmationStore(second)
        assert [c.id for c in store.pending()] == ["c1", "c2"]
        assert [c.id for c in store.pending("s2")] == ["c2"]
        c1 = store.get("c1")
        assert c1 is not None and c1 == confirmation("c1")
        assert store.resolve("c1", "approved", NOW) is True
        assert store.resolve("c1", "denied", NOW) is False  # only once
        c1 = store.get("c1")
        assert c1 is not None and c1.status == "approved"
        assert [c.id for c in store.due(NOW + timedelta(minutes=3))] == ["c2"]
        assert store.due(NOW) == []
        assert store.overrides() == {("test_messages", "send_test_message"): "irreversible"}
        [note] = store.notifications()
        assert (note.id, note.ref_id, note.created_at) == ("n1", "c1", NOW)
    finally:
        second.dispose()


def test_round_trip_sqlite(tmp_path: Path) -> None:
    round_trip(lambda: file_engine(tmp_path / "alfred.db"))


def test_round_trip_postgres(postgres_engines: Callable[[], Engine]) -> None:  # noqa: F811
    round_trip(postgres_engines)
