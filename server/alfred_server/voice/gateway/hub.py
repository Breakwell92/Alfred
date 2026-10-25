"""Which devices are connected, and which conversation each one is in.

One live connection per device: a reconnect (the phone changed network, the old socket
hasn't timed out yet) replaces the old one. A device keeps its conversation (`session_id`)
across reconnects, so a pending confirmation can still be answered. Held in memory: after a
server restart each device starts a new conversation unless its `hello` names one.
Pushes to a device (STU-84) will go through `get`.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class Replaceable(Protocol):
    async def replace(self) -> None:
        """Close this connection because the device has connected again."""
        ...


class DeviceHub:
    def __init__(self) -> None:
        self._connections: dict[str, Replaceable] = {}
        self._sessions: dict[str, str] = {}

    def session_for(self, device_id: str, requested: str | None = None) -> str:
        """The device's conversation: `requested` if given (and remembered), else its last one."""
        if requested is not None:
            self._sessions[device_id] = requested
            return requested
        return self._sessions.setdefault(device_id, str(uuid.uuid4()))

    async def register(self, device_id: str, connection: Replaceable) -> None:
        """Make `connection` the device's live one, closing any older one."""
        old = self._connections.get(device_id)
        self._connections[device_id] = connection
        if old is not None and old is not connection:
            await old.replace()

    def unregister(self, device_id: str, connection: Replaceable) -> None:
        """Forget `connection`, unless a newer one has already replaced it."""
        if self._connections.get(device_id) is connection:
            del self._connections[device_id]

    def get(self, device_id: str) -> Replaceable | None:
        return self._connections.get(device_id)
