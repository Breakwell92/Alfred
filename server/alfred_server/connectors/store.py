"""Connector state, encrypted credentials and OAuth tokens, persisted in Postgres.

The store only ever sees ciphertext from `Vault`; it never decrypts anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()

# One row per connector that has ever been changed. No row means disabled.
connector_state = Table(
    "connector_state",
    metadata,
    Column("connector_id", String(40), primary_key=True),
    Column("enabled", Boolean, nullable=False, default=False),
    Column("status", String(20), nullable=False),
    Column("last_error", Text, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# API key and OAuth client id/secret, as one Fernet token.
connector_credentials = Table(
    "connector_credentials",
    metadata,
    Column("connector_id", String(40), primary_key=True),
    Column("ciphertext", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# OAuth access/refresh tokens, as one Fernet token. `expires_at` is in clear for refresh scans.
connector_tokens = Table(
    "connector_tokens",
    metadata,
    Column("connector_id", String(40), primary_key=True),
    Column("ciphertext", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# Pending authorization requests: one-time `state` → connector and PKCE verifier (encrypted).
connector_oauth_states = Table(
    "connector_oauth_states",
    metadata,
    Column("state", String(64), primary_key=True),
    Column("connector_id", String(40), nullable=False),
    Column("ciphertext", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class StateRow:
    enabled: bool
    status: str
    last_error: str | None
    updated_at: datetime


@dataclass(frozen=True)
class TokenRow:
    ciphertext: str
    expires_at: datetime | None


@dataclass(frozen=True)
class OAuthStateRow:
    connector_id: str
    ciphertext: str
    created_at: datetime


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone; everything is stored in UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class ConnectorStore:
    """Reads and writes the connector tables. Creates them on first use (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._created = False

    def _ensure_tables(self) -> None:
        if not self._created:
            metadata.create_all(self.engine)
            self._created = True

    def _upsert(self, table: Table, key: dict[str, Any], values: dict[str, Any]) -> Any:
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            stmt: Any = pg_insert(table)
        elif dialect == "sqlite":
            stmt = sqlite_insert(table)
        else:
            raise NotImplementedError(f"unsupported database {dialect!r}")
        stmt = stmt.values(**key, **values)
        return stmt.on_conflict_do_update(index_elements=list(key), set_=values)

    # --- State ------------------------------------------------------------------------------

    def states(self) -> dict[str, StateRow]:
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(select(connector_state)).all()
        return {
            r.connector_id: StateRow(r.enabled, r.status, r.last_error, _aware(r.updated_at))  # type: ignore[arg-type]
            for r in rows
        }

    def state(self, connector_id: str) -> StateRow | None:
        return self.states().get(connector_id)

    def set_state(
        self, connector_id: str, *, enabled: bool, status: str, last_error: str | None = None
    ) -> None:
        self._ensure_tables()
        values = {
            "enabled": enabled,
            "status": status,
            "last_error": last_error,
            "updated_at": datetime.now(UTC),
        }
        with self.engine.begin() as conn:
            conn.execute(self._upsert(connector_state, {"connector_id": connector_id}, values))

    # --- Credentials ------------------------------------------------------------------------

    def credentials(self, connector_id: str) -> str | None:
        self._ensure_tables()
        with self.engine.connect() as conn:
            return conn.execute(
                select(connector_credentials.c.ciphertext).where(
                    connector_credentials.c.connector_id == connector_id
                )
            ).scalar_one_or_none()

    def set_credentials(self, connector_id: str, ciphertext: str | None) -> None:
        """Store (or with None, delete) a connector's encrypted credentials."""
        self._ensure_tables()
        with self.engine.begin() as conn:
            if ciphertext is None:
                conn.execute(
                    delete(connector_credentials).where(
                        connector_credentials.c.connector_id == connector_id
                    )
                )
                return
            values = {"ciphertext": ciphertext, "updated_at": datetime.now(UTC)}
            conn.execute(
                self._upsert(connector_credentials, {"connector_id": connector_id}, values)
            )

    # --- Tokens -----------------------------------------------------------------------------

    def tokens(self, connector_id: str) -> TokenRow | None:
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(
                select(connector_tokens.c.ciphertext, connector_tokens.c.expires_at).where(
                    connector_tokens.c.connector_id == connector_id
                )
            ).first()
        return None if row is None else TokenRow(row.ciphertext, _aware(row.expires_at))

    def set_tokens(self, connector_id: str, ciphertext: str, expires_at: datetime | None) -> None:
        self._ensure_tables()
        values = {
            "ciphertext": ciphertext,
            "expires_at": expires_at,
            "updated_at": datetime.now(UTC),
        }
        with self.engine.begin() as conn:
            conn.execute(self._upsert(connector_tokens, {"connector_id": connector_id}, values))

    def delete_tokens(self, connector_id: str) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                delete(connector_tokens).where(connector_tokens.c.connector_id == connector_id)
            )

    def tokens_expiring_before(self, when: datetime) -> list[str]:
        self._ensure_tables()
        with self.engine.connect() as conn:
            return list(
                conn.execute(
                    select(connector_tokens.c.connector_id).where(
                        connector_tokens.c.expires_at.is_not(None),
                        connector_tokens.c.expires_at < when,
                    )
                ).scalars()
            )

    # --- OAuth states -----------------------------------------------------------------------

    def add_oauth_state(
        self, state: str, connector_id: str, ciphertext: str, created_at: datetime | None = None
    ) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                connector_oauth_states.insert().values(
                    state=state,
                    connector_id=connector_id,
                    ciphertext=ciphertext,
                    created_at=created_at or datetime.now(UTC),
                )
            )

    def pop_oauth_state(self, state: str) -> OAuthStateRow | None:
        """Remove and return a pending authorization. Atomic, so each state works once."""
        self._ensure_tables()
        with self.engine.begin() as conn:
            row = conn.execute(
                delete(connector_oauth_states)
                .where(connector_oauth_states.c.state == state)
                .returning(
                    connector_oauth_states.c.connector_id,
                    connector_oauth_states.c.ciphertext,
                    connector_oauth_states.c.created_at,
                )
            ).first()
        if row is None:
            return None
        return OAuthStateRow(row.connector_id, row.ciphertext, _aware(row.created_at))  # type: ignore[arg-type]

    def purge_oauth_states(self, older_than: datetime) -> int:
        self._ensure_tables()
        with self.engine.begin() as conn:
            return conn.execute(
                delete(connector_oauth_states).where(
                    connector_oauth_states.c.created_at < older_than
                )
            ).rowcount
