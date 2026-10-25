"""The routine service: saved routines, matching an utterance to one, and running it.

`/chat`, `/chat/stream` and the voice gateway call `match_and_run` after a pending
confirmation has had its chance and before the router; the REST routes in `api.py` and the
`routines` specialist's tools call the rest. Design: docs/decisions/0021-routines.md.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine

from alfred_server.config import get_settings
from alfred_server.confirmations.policy import ToolPolicy
from alfred_server.routines.matcher import match
from alfred_server.routines.models import Routine, RoutineResult, RoutineSpec
from alfred_server.routines.runner import RoutineRunner, load_functions
from alfred_server.routines.store import RoutineStore
from alfred_server.routines.validation import (
    ROUTINES_CONNECTOR_ID,
    RoutineInvalidError,
    check_routine,
)

if TYPE_CHECKING:
    from alfred_server.agents.router import ToolLoader

logger = logging.getLogger(__name__)


class RoutineNotFoundError(KeyError):
    """No routine with that id (or name)."""


def _argument_problem(tool: str, args: dict[str, Any], parameters: dict[str, Any]) -> str | None:
    """Why `args` don't fit the tool's schema (missing or unknown arguments), or None."""
    properties = parameters.get("properties") or {}
    unknown = sorted(set(args) - set(properties))
    if unknown:
        return f"{tool} has no setting called {', '.join(unknown)}."
    missing = [name for name in parameters.get("required") or [] if name not in args]
    if missing:
        return f"{tool} needs {', '.join(missing)}."
    return None


class RoutineService:
    def __init__(
        self,
        store: RoutineStore,
        policy: Callable[[], ToolPolicy],
        loader: ToolLoader | None,
        *,
        runner: RoutineRunner | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        # Effective tool tiers (the confirmation service's, with Dan's overrides). Raises
        # `ConfirmationsUnavailableError` when they can't be read: routines fail closed.
        self.policy = policy
        self.loader = loader
        self.runner = runner or RoutineRunner(loader)
        self.clock = clock

    # --- Reading ----------------------------------------------------------------------------

    def all(self) -> list[Routine]:
        return self.store.all()

    def get(self, routine_id: str) -> Routine:
        routine = self.store.get(routine_id)
        if routine is None:
            raise RoutineNotFoundError(routine_id)
        return routine

    def find(self, name: str) -> Routine | None:
        """The routine called `name` (any case), or None."""
        wanted = name.strip().casefold()
        return next((r for r in self.store.all() if r.name.casefold() == wanted), None)

    # --- Changing ---------------------------------------------------------------------------

    def create(self, spec: RoutineSpec) -> Routine:
        """Validate and save a new routine. Raises `RoutineInvalidError`."""
        checked = check_routine(spec, self.policy(), self.store.all(), routine_id=None)
        now = self.clock()
        routine = Routine(
            **checked.model_dump(), id=str(uuid.uuid4()), created_at=now, updated_at=now
        )
        self.store.add(routine)
        logger.info("routine %r saved (%d steps)", routine.name, len(routine.steps))
        return routine

    def update(self, routine_id: str, spec: RoutineSpec) -> Routine:
        """Validate and overwrite a routine.

        Raises `RoutineNotFoundError` or `RoutineInvalidError`.
        """
        current = self.get(routine_id)
        checked = check_routine(spec, self.policy(), self.store.all(), routine_id=routine_id)
        routine = Routine(
            **checked.model_dump(),
            id=routine_id,
            created_at=current.created_at,
            updated_at=self.clock(),
        )
        if not self.store.replace(routine):
            raise RoutineNotFoundError(routine_id)
        logger.info("routine %r updated (%d steps)", routine.name, len(routine.steps))
        return routine

    def save(self, spec: RoutineSpec) -> Routine:
        """Create the routine, or replace the one with the same name."""
        existing = self.find(spec.name)
        return self.create(spec) if existing is None else self.update(existing.id, spec)

    def delete(self, routine_id: str) -> None:
        if not self.store.delete(routine_id):
            raise RoutineNotFoundError(routine_id)

    # --- Step tools -------------------------------------------------------------------------

    async def step_catalog(self) -> list[dict[str, Any]]:
        """The tools a routine may use now: every connected connector's, except `irreversible`
        ones and the routines connector's own. Each with its arguments' JSON schema and whether
        it normally asks first (then saving the routine approves it for that routine)."""
        policy = await asyncio.to_thread(self.policy)
        ids = {c.id for c in policy.registry.all() if c.id != ROUTINES_CONNECTOR_ID}
        functions = await load_functions(self.loader, policy, ids)
        catalog = []
        for name, function in sorted(functions.items()):
            found = policy.registry.tool_meta(name)
            if found is None:
                continue
            connector, meta = found
            tier = policy.tier(name)
            if meta.risk == "irreversible" or tier == "irreversible":
                continue
            catalog.append(
                {
                    "connector": connector.id,
                    "tool": name,
                    "description": meta.description,
                    "asks_first": tier != "read",
                    "parameters": function.parameters,
                }
            )
        return catalog

    async def check_arguments(self, spec: RoutineSpec) -> None:
        """Check each step's arguments against its tool's schema, when the tool is loaded now.

        Raises `RoutineInvalidError`. A tool whose connector isn't connected is checked at
        run time instead.
        """
        catalog = {t["tool"]: t for t in await self.step_catalog()}
        for index, step in enumerate(spec.steps, start=1):
            entry = catalog.get(step.tool)
            if entry is None:
                continue
            problem = _argument_problem(step.tool, step.args, entry["parameters"] or {})
            if problem:
                raise RoutineInvalidError(f"Step {index}: {problem}")

    # --- Running ----------------------------------------------------------------------------

    async def run(self, routine: Routine, trigger: str) -> RoutineResult:
        """Run `routine` now. Raises `ConfirmationsUnavailableError` if the policy can't be
        read (no step runs unchecked)."""
        policy = await asyncio.to_thread(self.policy)
        return await self.runner.run(routine, policy, trigger)

    async def run_by_id(self, routine_id: str, trigger: str) -> RoutineResult:
        """Run a saved routine (REST, and later the scheduler and triggers)."""
        routine = await asyncio.to_thread(self.get, routine_id)
        return await self.run(routine, trigger)

    async def match_and_run(self, text: str, trigger: str) -> RoutineResult | None:
        """Run the routine `text` triggers, or None if it triggers none.

        Routines are a fast path, not a gate: if they can't be read, `text` goes to the router.
        """
        try:
            routines = await asyncio.to_thread(self.store.all)
        except Exception:
            logger.exception("couldn't read routines; routing as usual")
            return None
        routine = match(text, routines)
        if routine is None:
            return None
        return await self.run(routine, trigger)


def build_routine_service() -> RoutineService:
    # Imported here: both build engines and registries on first use.
    from alfred_server.confirmations.service import get_confirmation_service
    from alfred_server.connectors.service import get_connector_service

    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    return RoutineService(
        RoutineStore(engine),
        get_confirmation_service().policy,
        get_connector_service().load_tools,
    )


@lru_cache
def get_routine_service() -> RoutineService:
    """The process-wide routine service. Use as a FastAPI dependency (overridden in tests)."""
    return build_routine_service()
