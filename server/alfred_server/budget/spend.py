"""Actual cloud spend, read from LiteLLM's spend logs.

LiteLLM writes a row per call to `"LiteLLM_SpendLogs"` in its own database (`litellm` in the
compose Postgres): cost in USD, the alias as `model_group`, a naive-UTC `startTime`, and the
`CallMetadata` from `alfred_server.models.gateway` under `metadata.spend_logs_metadata` (plus
`agent:`/`trigger:` request tags). Rows are written in batches, up to about a minute late.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import JSON, Column, DateTime, Engine, Float, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import JSONB

# Only the columns we read; the table itself is owned and migrated by LiteLLM.
_litellm_metadata = MetaData()
_json = JSON().with_variant(JSONB(), "postgresql")
spend_logs = Table(
    "LiteLLM_SpendLogs",
    _litellm_metadata,
    Column("request_id", String, primary_key=True),
    Column("spend", Float, nullable=False),
    Column("startTime", DateTime(timezone=False), nullable=False),
    Column("model_group", String),
    Column("request_tags", _json),
    Column("metadata", _json),
)


class SpendUnavailableError(RuntimeError):
    """The spend logs couldn't be read, so spend is unknown."""


@dataclass(frozen=True)
class SpendRow:
    start_time: datetime  # UTC, timezone-aware
    usd: float
    alias: str | None
    agent: str | None
    task_id: str | None
    trigger: str | None


class SpendSource(Protocol):
    def rows(self, start: datetime, end: datetime) -> list[SpendRow]:
        """Calls with non-zero spend that started in [start, end) (aware datetimes)."""
        ...


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _tag(tags: Any, prefix: str) -> str | None:
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith(prefix):
                return tag.removeprefix(prefix)
    return None


def _row(start_time: datetime, spend: float, alias: str | None, tags: Any, meta: Any) -> SpendRow:
    logged = meta.get("spend_logs_metadata") if isinstance(meta, dict) else None
    logged = logged if isinstance(logged, dict) else {}
    return SpendRow(
        start_time=start_time.replace(tzinfo=UTC) if start_time.tzinfo is None else start_time,
        usd=float(spend),
        alias=alias or None,
        agent=logged.get("agent") or _tag(tags, "agent:"),
        task_id=logged.get("task_id"),
        trigger=logged.get("trigger") or _tag(tags, "trigger:"),
    )


class LiteLLMSpendLogs:
    """`SpendSource` reading LiteLLM's spend log table."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def rows(self, start: datetime, end: datetime) -> list[SpendRow]:
        c = spend_logs.c
        query = (
            select(c.startTime, c.spend, c.model_group, c.request_tags, c.metadata)
            .where(c.startTime >= _naive_utc(start), c.startTime < _naive_utc(end), c.spend > 0)
            .order_by(c.startTime)
        )
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query).all()
        except Exception as exc:  # any DB failure means spend is unknown
            raise SpendUnavailableError(f"can't read LiteLLM spend logs: {exc}") from exc
        return [_row(*r) for r in result]
