"""`FakeHomeAssistant`: a recording stand-in for `HomeAssistantMcp` with canned HA answers."""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent

from alfred_server.connectors.builtin.home_assistant import (
    HomeAssistantError,
    HomeAssistantTools,
)

LIVE_CONTEXT = (
    "Live Context: An overview of the areas and the devices in this smart home:\n"
    "- names: Lounge Lights\n  domain: light\n  state: 'off'\n  areas: Lounge\n"
    "- names: Back Door\n  domain: lock\n  state: locked\n  areas: Kitchen\n"
    "- names: Thermostat\n  domain: climate\n  state: heat\n  areas: Hall\n"
    "  attributes:\n    current_temperature: 18.5\n    temperature: 20\n"
)


def intent_done(*names: str) -> CallToolResult:
    """HA's answer to a successful intent, acting on `names`."""
    body = {
        "speech": {"plain": {"speech": "Done"}},
        "response_type": "action_done",
        "data": {
            "targets": [],
            "success": [{"name": n, "type": "entity", "id": n.lower()} for n in names],
            "failed": [],
        },
    }
    return result(json.dumps(body))


def result(text: str, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=error)


class FakeHomeAssistant:
    """Answers `call(tool, args)` from `answers` (tool name → result), recording each call."""

    def __init__(self, answers: dict[str, CallToolResult] | None = None) -> None:
        self.answers = {
            "GetLiveContext": result(json.dumps({"success": True, "result": LIVE_CONTEXT})),
            **(answers or {}),
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: HomeAssistantError | None = None

    async def call(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((tool, dict(arguments)))
        if self.fail is not None:
            raise self.fail
        return self.answers.get(tool) or intent_done("Lounge Lights")

    def tools(self) -> HomeAssistantTools:
        return HomeAssistantTools(self)
