"""Mock mode: every tool call an eval makes is recorded and answered from a canned fixture.

The real tool never runs, so evals change nothing (no lights, no emails) and give the same tool
results every time. Fixtures are in `evals/fixtures/tools.yaml`: a string is formatted with
the request's `now` and `timezone` and the call's arguments (`"{now:%H:%M}"`, `"{room}"`); any
other value is sent as JSON. A tool without a fixture gets `{"ok": true}` and is flagged as
unmocked in the report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from agno.tools import Toolkit

from alfred_server.agents.context import RequestContext
from alfred_server.connectors.models import AgnoToolkitSource, Credentials
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.connectors.service import restrict_toolkit

FIXTURES_FILE = Path(__file__).parent / "fixtures" / "tools.yaml"
UNMOCKED_RESULT = '{"ok": true}'


def load_fixtures(path: Path = FIXTURES_FILE) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping of tool name to fixture")
    return raw


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    mocked: bool


@dataclass
class MockTools:
    """An Agno tool hook (`Agent.tool_hooks`) that answers calls from fixtures."""

    fixtures: dict[str, Any]
    ctx: RequestContext
    calls: list[ToolCall] = field(default_factory=list)

    def result(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.fixtures:
            return UNMOCKED_RESULT
        fixture = self.fixtures[name]
        if isinstance(fixture, str):
            try:
                return fixture.format(now=self.ctx.now, timezone=self.ctx.timezone, **args)
            except (KeyError, IndexError, ValueError):
                return fixture  # an argument the model left out: send it unformatted
        return json.dumps(fixture)

    def __call__(self, function_name: str, arguments: dict[str, Any]) -> str:
        # The hook never calls the real function (Agno passes it as `function_call`).
        args = dict(arguments or {})
        self.calls.append(ToolCall(function_name, args, function_name in self.fixtures))
        return self.result(function_name, args)


@dataclass(frozen=True)
class MockToolLoader:
    """The router's connector-tool loader for evals: every toolkit connector of a specialist,
    connected or not, built with empty credentials. Its calls are answered by `MockTools`, so
    no service is contacted. MCP connectors are skipped (their tools need a live server).
    """

    registry: ConnectorRegistry = field(default_factory=default_registry)

    async def __call__(self, specialist: str) -> list[Toolkit]:
        loaded = []
        for connector in self.registry.for_specialist(specialist):
            if not isinstance(connector.tool_source, AgnoToolkitSource):
                continue
            toolkit = connector.tool_source.factory(Credentials())
            loaded.append(restrict_toolkit(toolkit, set(connector.tool_names)))
        return loaded
