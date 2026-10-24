"""Background task that renews OAuth tokens before they expire."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from alfred_server.connectors.service import ConnectorService

logger = logging.getLogger(__name__)


class TokenRefresher:
    """Calls `service.refresh_due()` every `interval_s` until stopped."""

    def __init__(self, service: ConnectorService, interval_s: float) -> None:
        self.service = service
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="connector-token-refresher")

    async def _run(self) -> None:
        while True:
            try:
                refreshed = await asyncio.to_thread(self.service.refresh_due)
                if refreshed:
                    logger.info("refreshed connector tokens: %s", ", ".join(refreshed))
            except Exception:  # e.g. Postgres down: try again next tick
                logger.exception("connector token refresh failed")
            await asyncio.sleep(self.interval_s)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
