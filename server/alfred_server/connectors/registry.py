"""The connector registry: every connector Alfred knows about, by id.

Built-in connectors are registered in `default_registry()`. Runtime state (enabled, status,
credentials) is separate, in `ConnectorStore`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from alfred_server.connectors.models import Connector, ToolMeta

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


class ConnectorRegistry:
    def __init__(self, connectors: Iterable[Connector] = ()) -> None:
        self._connectors: dict[str, Connector] = {}
        self._tools: dict[str, tuple[Connector, ToolMeta]] = {}
        for connector in connectors:
            self.register(connector)

    def register(self, connector: Connector) -> None:
        """Add `connector`. Raises `ValueError` if its definition is inconsistent."""
        if not _ID.match(connector.id):
            raise ValueError(f"connector id {connector.id!r} must match {_ID.pattern}")
        if connector.id in self._connectors:
            raise ValueError(f"connector {connector.id!r} is already registered")
        if (connector.auth_type == "oauth2") != (connector.oauth is not None):
            raise ValueError(f"connector {connector.id!r}: oauth config iff auth_type is oauth2")
        if not connector.tools:
            raise ValueError(f"connector {connector.id!r} declares no tools")
        names = connector.tool_names
        if len(set(names)) != len(names):
            raise ValueError(f"connector {connector.id!r} declares a tool twice")
        # Tool names are global: risk gating and privacy routing look tools up by name alone.
        for tool in connector.tools:
            if tool.name in self._tools:
                owner = self._tools[tool.name][0].id
                raise ValueError(f"tool {tool.name!r} is already declared by {owner!r}")
        self._connectors[connector.id] = connector
        for tool in connector.tools:
            self._tools[tool.name] = (connector, tool)

    def get(self, connector_id: str) -> Connector | None:
        return self._connectors.get(connector_id)

    def all(self) -> list[Connector]:
        return list(self._connectors.values())

    def for_specialist(self, specialist: str) -> list[Connector]:
        return [c for c in self._connectors.values() if c.specialist == specialist]

    def tool_meta(self, tool_name: str) -> tuple[Connector, ToolMeta] | None:
        """The connector and risk/privacy metadata of a loaded tool, by tool name."""
        return self._tools.get(tool_name)


def default_registry() -> ConnectorRegistry:
    """A registry holding every built-in connector."""
    from alfred_server.connectors.builtin import BUILTIN_CONNECTORS

    return ConnectorRegistry(BUILTIN_CONNECTORS)
