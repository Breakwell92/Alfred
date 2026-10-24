"""Budget settings and per-month state, persisted in the server's Postgres database."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from alfred_server.budget.models import BudgetSettings

metadata = MetaData()

# One row (id 1) holding the whole BudgetSettings document.
budget_settings = Table(
    "budget_settings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("settings", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# One row per month (`YYYY-MM`) once anything is recorded for it.
budget_months = Table(
    "budget_months",
    metadata,
    Column("month", String(7), primary_key=True),
    Column("over_budget_announced_at", DateTime(timezone=True), nullable=True),
)

_SETTINGS_ID = 1


class BudgetStore:
    """Reads and writes the budget tables. Creates them on first use (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._created = False

    def _ensure_tables(self) -> None:
        if not self._created:
            metadata.create_all(self.engine)
            self._created = True

    def load(self) -> BudgetSettings:
        """The saved settings, or the seed defaults if nothing has been saved yet."""
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(
                select(budget_settings.c.settings).where(budget_settings.c.id == _SETTINGS_ID)
            ).first()
        if row is None:
            return BudgetSettings()
        return BudgetSettings.model_validate(row.settings)

    def save(self, settings: BudgetSettings) -> None:
        self._ensure_tables()
        values = {"settings": settings.model_dump(mode="json"), "updated_at": datetime.now(UTC)}
        with self.engine.begin() as conn:
            updated = conn.execute(
                update(budget_settings).where(budget_settings.c.id == _SETTINGS_ID).values(values)
            ).rowcount
            if not updated:
                conn.execute(insert(budget_settings).values(id=_SETTINGS_ID, **values))

    def mark_over_budget_announced(self, month: str, at: datetime | None = None) -> bool:
        """Record that the user was told the budget for `month` is used up.

        Returns True if this is the first time for that month (so the caller should tell
        them), False if they've already been told.
        """
        self._ensure_tables()
        at = at or datetime.now(UTC)
        with self.engine.begin() as conn:
            row = conn.execute(
                select(budget_months.c.over_budget_announced_at)
                .where(budget_months.c.month == month)
                .with_for_update()
            ).first()
            if row is None:
                conn.execute(insert(budget_months).values(month=month, over_budget_announced_at=at))
                return True
            if row.over_budget_announced_at is None:
                conn.execute(
                    update(budget_months)
                    .where(budget_months.c.month == month)
                    .values(over_budget_announced_at=at)
                )
                return True
            return False
