"""Pending confirmations, per-tool permission overrides and notifications, in Postgres."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from alfred_server.confirmations.models import ConfirmationStatus, PendingConfirmation, Tier

metadata = MetaData()

# One row per paused run. Rows are kept after they're resolved, as a record of what was asked.
confirmations = Table(
    "confirmations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("session_id", String(128), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("tier", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("summary", Text, nullable=False),
    # Everything else in `PendingConfirmation` (specialist, alias, tools, device), as JSON.
    Column("record", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
)

# Dan's per-tool overrides of the connector's declared risk. No row = the declared risk.
tool_permissions = Table(
    "tool_permissions",
    metadata,
    Column("connector_id", String(40), primary_key=True),
    Column("tool_name", String(128), primary_key=True),
    Column("risk", String(16), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# Things to tell Dan about unprompted (push delivery comes later; STU-84).
notifications = Table(
    "notifications",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("ref_id", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True), nullable=True),
)


@dataclass(frozen=True)
class NotificationRow:
    id: str
    kind: str
    title: str
    body: str
    ref_id: str | None
    created_at: datetime


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; everything is stored in UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ConfirmationStore:
    """Reads and writes the confirmation tables. Creates them on first use (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._created = False

    def _ensure_tables(self) -> None:
        if not self._created:
            metadata.create_all(self.engine)
            self._created = True

    # --- Confirmations ----------------------------------------------------------------------

    def add(self, c: PendingConfirmation) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                confirmations.insert().values(
                    id=c.id,
                    run_id=c.run_id,
                    session_id=c.session_id,
                    status=c.status,
                    tier=c.tier,
                    channel=c.channel,
                    summary=c.summary,
                    record=c.model_dump_json(),
                    created_at=c.created_at,
                    expires_at=c.expires_at,
                )
            )

    @staticmethod
    def _row(row: Any) -> PendingConfirmation:
        record = PendingConfirmation.model_validate(json.loads(row.record))
        return record.model_copy(update={"status": row.status})

    def get(self, confirmation_id: str) -> PendingConfirmation | None:
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(
                select(confirmations).where(confirmations.c.id == confirmation_id)
            ).first()
        return None if row is None else self._row(row)

    def pending(self, session_id: str | None = None) -> list[PendingConfirmation]:
        """Pending confirmations, oldest first (expired ones included until marked)."""
        self._ensure_tables()
        query = select(confirmations).where(confirmations.c.status == "pending")
        if session_id is not None:
            query = query.where(confirmations.c.session_id == session_id)
        with self.engine.connect() as conn:
            rows = conn.execute(query.order_by(confirmations.c.created_at)).all()
        return [self._row(r) for r in rows]

    def resolve(self, confirmation_id: str, status: ConfirmationStatus, when: datetime) -> bool:
        """Move a pending confirmation to `status`. False if it wasn't pending any more.

        Atomic, so two answers arriving together can't both resume the run.
        """
        self._ensure_tables()
        with self.engine.begin() as conn:
            result = conn.execute(
                update(confirmations)
                .where(confirmations.c.id == confirmation_id, confirmations.c.status == "pending")
                .values(status=status, resolved_at=when)
            )
        return bool(result.rowcount)

    def due(self, now: datetime) -> list[PendingConfirmation]:
        """Pending confirmations that have expired."""
        return [c for c in self.pending() if _aware(c.expires_at) <= now]

    # --- Permission overrides ---------------------------------------------------------------

    def overrides(self) -> dict[tuple[str, str], Tier]:
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(select(tool_permissions)).all()
        return {(r.connector_id, r.tool_name): r.risk for r in rows}

    def set_override(self, connector_id: str, tool_name: str, risk: Tier | None) -> None:
        """Store (or with None, remove) the override for one tool."""
        self._ensure_tables()
        key = {"connector_id": connector_id, "tool_name": tool_name}
        with self.engine.begin() as conn:
            if risk is None:
                conn.execute(
                    delete(tool_permissions).where(
                        tool_permissions.c.connector_id == connector_id,
                        tool_permissions.c.tool_name == tool_name,
                    )
                )
                return
            values = {"risk": risk, "updated_at": datetime.now(UTC)}
            dialect = self.engine.dialect.name
            if dialect == "postgresql":
                stmt: Any = pg_insert(tool_permissions)
            elif dialect == "sqlite":
                stmt = sqlite_insert(tool_permissions)
            else:
                raise NotImplementedError(f"unsupported database {dialect!r}")
            stmt = stmt.values(**key, **values).on_conflict_do_update(
                index_elements=list(key), set_=values
            )
            conn.execute(stmt)

    # --- Notifications ----------------------------------------------------------------------

    def add_notification(self, row: NotificationRow) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                notifications.insert().values(
                    id=row.id,
                    kind=row.kind,
                    title=row.title,
                    body=row.body,
                    ref_id=row.ref_id,
                    created_at=row.created_at,
                )
            )

    def notifications(self) -> list[NotificationRow]:
        """Every notification, oldest first."""
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(select(notifications).order_by(notifications.c.created_at)).all()
        return [
            NotificationRow(r.id, r.kind, r.title, r.body, r.ref_id, _aware(r.created_at))
            for r in rows
        ]
