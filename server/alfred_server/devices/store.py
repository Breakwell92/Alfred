"""Paired devices and one-time pairing codes, in Postgres."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, Engine, MetaData, String, Table, Text, select, update

metadata = MetaData()

# One row per paired app install. Revoked rows are kept, as a record.
paired_devices = Table(
    "paired_devices",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(64), nullable=False),
    # Base64 X.509 SubjectPublicKeyInfo (DER) of the device's EC P-256 key.
    Column("public_key", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

# Codes minted by `python -m alfred_server.devices pair`. Only the SHA-256 of a code is kept.
pairing_codes = Table(
    "pairing_codes",
    metadata,
    Column("code_hash", String(64), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)


@dataclass(frozen=True)
class PairedDevice:
    id: str
    name: str
    public_key: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone; everything is stored in UTC.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _device(row: Any) -> PairedDevice:
    return PairedDevice(
        id=row.id,
        name=row.name,
        public_key=row.public_key,
        created_at=_aware(row.created_at) or row.created_at,
        last_used_at=_aware(row.last_used_at),
        revoked_at=_aware(row.revoked_at),
    )


class DeviceStore:
    """Reads and writes the device tables. Creates them on first use (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._created = False

    def _ensure_tables(self) -> None:
        if not self._created:
            metadata.create_all(self.engine)
            self._created = True

    # --- Pairing codes ----------------------------------------------------------------------

    def add_code(self, code_hash: str, name: str, now: datetime, expires_at: datetime) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                pairing_codes.insert().values(
                    code_hash=code_hash, name=name, created_at=now, expires_at=expires_at
                )
            )

    def claim_code(self, code_hash: str, now: datetime) -> str | None:
        """Use up an unexpired, unused code. Returns its device name, or None.

        Atomic, so a code can pair only one device even if two requests race.
        """
        self._ensure_tables()
        with self.engine.begin() as conn:
            result = conn.execute(
                update(pairing_codes)
                .where(
                    pairing_codes.c.code_hash == code_hash,
                    pairing_codes.c.used_at.is_(None),
                    pairing_codes.c.expires_at > now,
                )
                .values(used_at=now)
            )
            if not result.rowcount:
                return None
            name = conn.execute(
                select(pairing_codes.c.name).where(pairing_codes.c.code_hash == code_hash)
            ).scalar_one()
        return str(name)

    # --- Devices ----------------------------------------------------------------------------

    def add_device(self, device: PairedDevice) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                paired_devices.insert().values(
                    id=device.id,
                    name=device.name,
                    public_key=device.public_key,
                    created_at=device.created_at,
                )
            )

    def get(self, device_id: str) -> PairedDevice | None:
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(
                select(paired_devices).where(paired_devices.c.id == device_id)
            ).first()
        return None if row is None else _device(row)

    def all(self) -> list[PairedDevice]:
        """Every device, oldest first (revoked ones included)."""
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(select(paired_devices).order_by(paired_devices.c.created_at)).all()
        return [_device(r) for r in rows]

    def touch(self, device_id: str, now: datetime) -> None:
        self._ensure_tables()
        with self.engine.begin() as conn:
            conn.execute(
                update(paired_devices)
                .where(paired_devices.c.id == device_id)
                .values(last_used_at=now)
            )

    def revoke(self, device_id: str, now: datetime) -> bool:
        """Revoke a device. False if there's no such device or it was already revoked."""
        self._ensure_tables()
        with self.engine.begin() as conn:
            result = conn.execute(
                update(paired_devices)
                .where(paired_devices.c.id == device_id, paired_devices.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )
        return bool(result.rowcount)
