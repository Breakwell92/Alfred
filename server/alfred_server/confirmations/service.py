"""The confirmation service: records paused runs, answers "may this channel approve it?",
expires stale ones and cancels their runs. Resuming a run is the router's job (`Router.resume`).

Design and rules: docs/decisions/0013-confirmation-gating.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal

from agno.db.base import AsyncBaseDb, BaseDb
from agno.run.agent import RunOutput, RunPausedEvent
from agno.run.base import RunStatus
from sqlalchemy import create_engine

from alfred_server.agents.context import RequestContext
from alfred_server.config import Settings, get_settings
from alfred_server.confirmations.models import CHANNEL_FOR_DEVICE, PendingConfirmation, Tier
from alfred_server.confirmations.policy import (
    ToolPolicy,
    highest,
    join_summaries,
    summarise_call,
)
from alfred_server.confirmations.store import ConfirmationStore, NotificationRow
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.models.gateway import ModelAlias

logger = logging.getLogger(__name__)

YesNo = Literal["yes", "no"]

_YES = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|confirm(?:ed)?|approve(?:d)?|"
    r"send it|please do|yes please|go for it)$"
)
_NO = re.compile(
    r"^(?:no|nope|nah|don'?t|do not|cancel|stop|deny|denied|no thanks|no thank you|"
    r"don'?t send it|never mind|forget it)$"
)

# What's said for the irreversible tier on a channel that can't approve it.
APP_ONLY_REPLY = "That one needs approving in the Alfred app."


class ConfirmationsUnavailableError(RuntimeError):
    """The confirmation tables can't be read or written (Postgres down)."""


class IrreversibleRelaxError(PermissionError):
    """A change would take a tool out of `irreversible` without the server CLI.

    Over the API it would let the shared API token undo what only a signed `app_ui` approval
    may do (ADR 0016), so it's allowed only from `python -m alfred_server.confirmations`.
    """


def parse_yes_no(text: str) -> YesNo | None:
    """ "yes" or "no" if the whole message is a plain answer, else None."""
    words = re.sub(r"[^\w\s']", " ", text.lower().replace("’", "'"))
    words = re.sub(r"\s+", " ", words).strip()
    words = re.sub(r"^(?:alfred )?(.*?)(?: alfred)?$", r"\1", words)
    if _YES.match(words):
        return "yes"
    if _NO.match(words):
        return "no"
    return None


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def ask_prompt(c: PendingConfirmation) -> str:
    """What the reply says when a run pauses for `c`."""
    if c.tier == "irreversible":
        return f"I need your approval first: {c.summary}. Please approve it in the Alfred app."
    return f"I need your OK first: {c.summary}. Shall I go ahead?"


def denied_reply(c: PendingConfirmation) -> str:
    return f"OK, I won't {_lower_first(c.summary)}."


def expired_reply(c: PendingConfirmation) -> str:
    return f"I didn't hear back, so I didn't {_lower_first(c.summary)}."


class ConfirmationService:
    def __init__(
        self,
        store: ConfirmationStore,
        registry: ConnectorRegistry,
        *,
        ttl: timedelta = timedelta(minutes=2),
        scheduled_ttl: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.registry = registry
        self.ttl = ttl
        self.scheduled_ttl = scheduled_ttl
        self.clock = clock

    # --- Policy -----------------------------------------------------------------------------

    def policy(self) -> ToolPolicy:
        """Effective tool tiers. Raises `ConfirmationsUnavailableError` (fail closed)."""
        try:
            overrides = self.store.overrides()
        except Exception as exc:
            raise ConfirmationsUnavailableError("Permission settings are unavailable") from exc
        return ToolPolicy(self.registry, overrides)

    def set_override(
        self,
        connector_id: str,
        tool_name: str,
        risk: Tier | None,
        *,
        allow_relaxing_irreversible: bool = False,
    ) -> None:
        """Store (or with None, remove) Dan's override for one declared tool.

        A change that takes the tool's effective tier from `irreversible` to anything lower
        raises `IrreversibleRelaxError` unless `allow_relaxing_irreversible` (the server CLI).
        Raises `KeyError` for a tool no connector declares.
        """
        connector = self.registry.get(connector_id)
        meta = (
            next((t for t in connector.tools if t.name == tool_name), None) if connector else None
        )
        if meta is None:
            raise KeyError(f"{connector_id}/{tool_name}")
        if not allow_relaxing_irreversible:
            current = self.store.overrides().get((connector_id, tool_name), meta.tier)
            if current == "irreversible" and (risk or meta.tier) != "irreversible":
                raise IrreversibleRelaxError(
                    f"{connector_id}/{tool_name} is irreversible; relax it on the server with "
                    "python -m alfred_server.confirmations set"
                )
        self.store.set_override(connector_id, tool_name, risk)

    # --- Records ----------------------------------------------------------------------------

    def create(
        self,
        run: RunOutput | RunPausedEvent,
        *,
        specialist: str,
        alias: ModelAlias,
        ctx: RequestContext,
        policy: ToolPolicy,
    ) -> PendingConfirmation:
        """Record the paused `run`, and for an unattended run a notification for Dan."""
        tools = [
            r.tool_execution
            for r in run.active_requirements
            if r.needs_confirmation and r.tool_execution is not None
        ]
        names = [t.tool_name or "unknown" for t in tools]
        summary = join_summaries(
            [summarise_call(policy, t.tool_name or "", t.tool_args) for t in tools]
        )
        channel = CHANNEL_FOR_DEVICE[ctx.device_type]
        now = self.clock()
        ttl = self.scheduled_ttl if channel == "scheduled" else self.ttl
        confirmation = PendingConfirmation(
            id=str(uuid.uuid4()),
            run_id=run.run_id or "",
            session_id=ctx.session_id,
            specialist=specialist,
            alias=alias,
            tools=names,
            summary=summary or "Run a tool",
            tier=highest(policy.tier(n) for n in names),
            channel=channel,
            device_type=ctx.device_type,
            device_id=ctx.device_id,
            created_at=now,
            expires_at=now + ttl,
        )
        self.store.add(confirmation)
        if channel == "scheduled":
            self.store.add_notification(
                NotificationRow(
                    id=str(uuid.uuid4()),
                    kind="confirmation",
                    title="A scheduled task needs your approval",
                    body=confirmation.summary,
                    ref_id=confirmation.id,
                    created_at=now,
                )
            )
        logger.info(
            "run %s paused for confirmation %s (%s: %s)",
            confirmation.run_id,
            confirmation.id,
            confirmation.tier,
            ", ".join(names),
        )
        return confirmation

    def get(self, confirmation_id: str) -> PendingConfirmation | None:
        return self.store.get(confirmation_id)

    def current_tier(self, c: PendingConfirmation) -> Tier:
        """`c`'s tier now: the higher of its tier when it paused and its tools' current tiers.

        Dan may have tightened a tool (e.g. `write` → `irreversible`) while it waited; the
        approval must then meet the new tier. Relaxing never lowers it. Raises
        `ConfirmationsUnavailableError` if the overrides can't be read.
        """
        policy = self.policy()
        return highest([c.tier, *(policy.tier(name) for name in c.tools)])

    def is_expired(self, c: PendingConfirmation) -> bool:
        expires_at = c.expires_at if c.expires_at.tzinfo else c.expires_at.replace(tzinfo=UTC)
        return expires_at <= self.clock()

    def pending(self, session_id: str | None = None) -> list[PendingConfirmation]:
        """Pending confirmations that haven't expired yet."""
        return [c for c in self.store.pending(session_id) if not self.is_expired(c)]

    def resolve(
        self, c: PendingConfirmation, status: Literal["approved", "denied", "expired", "cancelled"]
    ) -> bool:
        """Claim `c` with `status`. False if something else answered it first."""
        return self.store.resolve(c.id, status, self.clock())

    async def expire_due(self, db: BaseDb | AsyncBaseDb) -> list[PendingConfirmation]:
        """Expire every overdue confirmation and cancel its run. Returns the ones expired."""
        due = await asyncio.to_thread(self.store.due, self.clock())
        expired = []
        for c in due:
            if await asyncio.to_thread(self.resolve, c, "expired"):
                await cancel_paused_run(db, c, expired_reply(c))
                expired.append(c)
                logger.info("confirmation %s expired; run %s cancelled", c.id, c.run_id)
        return expired

    def notifications(self) -> list[NotificationRow]:
        return self.store.notifications()


async def cancel_paused_run(db: BaseDb | AsyncBaseDb, c: PendingConfirmation, reason: str) -> None:
    """Mark the paused Agno run cancelled, so it's left out of later turns' history.

    No model call: the gated tool never ran and the reply is fixed. A failure is logged, not
    raised: the confirmation is already resolved, so the run can never be resumed anyway.
    """
    try:
        if isinstance(db, AsyncBaseDb):
            run = await db.get_run(c.run_id)
        else:
            run = await asyncio.to_thread(db.get_run, c.run_id)
        if not isinstance(run, RunOutput):
            logger.warning("paused run %s not found to cancel", c.run_id)
            return
        if run.status != RunStatus.paused:
            return
        run.status = RunStatus.cancelled
        run.content = reason
        if isinstance(db, AsyncBaseDb):
            await db.upsert_run(run, session_id=c.session_id)
        else:
            await asyncio.to_thread(db.upsert_run, run, session_id=c.session_id)
    except Exception:
        logger.exception("couldn't cancel paused run %s", c.run_id)


def build_confirmation_service(settings: Settings | None = None) -> ConfirmationService:
    settings = settings or get_settings()
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    return ConfirmationService(
        ConfirmationStore(engine),
        default_registry(),
        ttl=timedelta(seconds=settings.confirmation_ttl_s),
        scheduled_ttl=timedelta(seconds=settings.confirmation_scheduled_ttl_s),
    )


@lru_cache
def get_confirmation_service() -> ConfirmationService:
    """The process-wide confirmation service. Use as a FastAPI dependency (overridden in tests)."""
    return build_confirmation_service()
