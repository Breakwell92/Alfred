"""Background task that cancels confirmations nobody answered in time."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from agno.db.base import AsyncBaseDb, BaseDb

from alfred_server.confirmations.service import ConfirmationService

logger = logging.getLogger(__name__)


class ConfirmationSweeper:
    """Calls `service.expire_due(db)` every `interval_s` until stopped."""

    def __init__(
        self, service: ConfirmationService, db: BaseDb | AsyncBaseDb, interval_s: float
    ) -> None:
        self.service = service
        self.db = db
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="confirmation-sweeper")

    async def _run(self) -> None:
        while True:
            # Sleep first: nothing can be due at startup, and a short-lived app (tests) never
            # touches the database.
            await asyncio.sleep(self.interval_s)
            try:
                await self.service.expire_due(self.db)
            except Exception as exc:  # e.g. Postgres down: try again next tick
                logger.warning("expiring confirmations failed: %s", exc)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
