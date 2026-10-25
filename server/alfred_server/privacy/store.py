"""Privacy settings, persisted in the server's Postgres database."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Table,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from alfred_server.privacy.models import PrivacySettings

metadata = MetaData()

# One row (id 1) holding the whole PrivacySettings document.
privacy_settings = Table(
    "privacy_settings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("settings", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

_SETTINGS_ID = 1


class PrivacyStore:
    """Reads and writes the privacy settings table. Creates it on first use (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._created = False

    def _ensure_tables(self) -> None:
        if not self._created:
            metadata.create_all(self.engine)
            self._created = True

    def load(self) -> PrivacySettings:
        """The saved settings, or the seed defaults if nothing has been saved yet."""
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(
                select(privacy_settings.c.settings).where(privacy_settings.c.id == _SETTINGS_ID)
            ).first()
        if row is None:
            return PrivacySettings()
        return PrivacySettings.model_validate(row.settings)

    def save(self, settings: PrivacySettings) -> None:
        self._ensure_tables()
        values = {"settings": settings.model_dump(mode="json"), "updated_at": datetime.now(UTC)}
        with self.engine.begin() as conn:
            updated = conn.execute(
                update(privacy_settings).where(privacy_settings.c.id == _SETTINGS_ID).values(values)
            ).rowcount
            if not updated:
                conn.execute(insert(privacy_settings).values(id=_SETTINGS_ID, **values))
