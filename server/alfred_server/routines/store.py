"""Routines in Postgres: one row per routine, the definition as JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Engine, MetaData, String, Table, Text, select

from alfred_server.routines.models import Routine

metadata = MetaData()

routines = Table(
    "routines",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(80), nullable=False, unique=True),
    Column("enabled", Boolean, nullable=False),
    # Everything else in `Routine` (phrases, steps, reply, approved tools), as JSON.
    Column("record", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; everything is stored in UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RoutineStore:
    """Reads and writes the `routines` table. Creates it on first use (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._created = False

    def _ensure_tables(self) -> None:
        if not self._created:
            metadata.create_all(self.engine)
            self._created = True

    @staticmethod
    def _row(row: Any) -> Routine:
        routine = Routine.model_validate_json(row.record)
        return routine.model_copy(
            update={"created_at": _aware(row.created_at), "updated_at": _aware(row.updated_at)}
        )

    @staticmethod
    def _values(r: Routine) -> dict[str, Any]:
        return {
            "name": r.name,
            "enabled": r.enabled,
            "record": r.model_dump_json(),
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }

    def all(self) -> list[Routine]:
        """Every routine, by name."""
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(select(routines).order_by(routines.c.name)).all()
        return [self._row(r) for r in rows]

    def get(self, routine_id: str) -> Routine | None:
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(select(routines).where(routines.c.id == routine_id)).first()
        return None if row is None else self._row(row)

    def add(self, r: Routine) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(routines.insert().values(id=r.id, **self._values(r)))

    def replace(self, r: Routine) -> bool:
        """Overwrite routine `r.id`. False if there's none."""
        self._ensure_tables()
        with self.engine.begin() as conn:
            result = conn.execute(
                routines.update().where(routines.c.id == r.id).values(**self._values(r))
            )
        return bool(result.rowcount)

    def delete(self, routine_id: str) -> bool:
        self._ensure_tables()
        with self.engine.begin() as conn:
            result = conn.execute(routines.delete().where(routines.c.id == routine_id))
        return bool(result.rowcount)
