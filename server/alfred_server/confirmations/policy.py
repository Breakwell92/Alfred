"""Which tools need confirmation, and marking them on an agent before it runs.

A tool's tier is Dan's override if he set one, else its connector's declared risk. A tool
nothing declares is treated as `irreversible` (fail closed). Gated tools are marked with
Agno's `requires_confirmation`, so Agno pauses the run before calling them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from agno.agent import Agent
from agno.tools import Toolkit
from agno.tools.function import Function

from alfred_server.confirmations.models import TIER_ORDER, Tier
from alfred_server.connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)

# Tier of a tool no connector declares.
UNDECLARED_TIER: Tier = "irreversible"


class ToolPolicy:
    """Effective tiers: `overrides` maps (connector id, tool name) to a tier."""

    def __init__(
        self, registry: ConnectorRegistry, overrides: Mapping[tuple[str, str], Tier] | None = None
    ) -> None:
        self.registry = registry
        self.overrides = dict(overrides or {})

    def tier(self, tool_name: str) -> Tier:
        found = self.registry.tool_meta(tool_name)
        if found is None:
            return UNDECLARED_TIER
        connector, meta = found
        return self.overrides.get((connector.id, tool_name), meta.risk)

    def gated(self, tool_name: str) -> bool:
        return self.tier(tool_name) != "read"

    def gated_names(self) -> list[str]:
        """Every declared tool that needs confirmation."""
        return [
            meta.name
            for connector in self.registry.all()
            for meta in connector.tools
            if self.gated(meta.name)
        ]


def highest(tiers: Iterable[Tier]) -> Tier:
    return max(tiers, key=lambda t: TIER_ORDER[t], default="read")


def _gate_toolkit(toolkit: Toolkit, policy: ToolPolicy) -> None:
    # The flag is rewritten every time, never only set: MCP toolkits are cached and shared
    # across requests, so a tool relaxed to `read` since the last request must lose it.
    for registry in (toolkit.functions, toolkit.async_functions):
        for name, function in registry.items():
            function.requires_confirmation = policy.gated(name)
    # Tools the toolkit registers later (e.g. an MCP server connecting during the run) take
    # their flag from this list.
    toolkit.requires_confirmation_tools = policy.gated_names()


def _gate_function(function: Function, policy: ToolPolicy) -> Function:
    function.requires_confirmation = policy.gated(function.name)
    return function


def gate_agent(agent: Agent, policy: ToolPolicy) -> Agent:
    """Mark every tool on `agent` that needs confirmation. Returns the same agent."""
    gated: list[Any] = []
    tools = agent.tools if isinstance(agent.tools, list) else []
    for tool in tools:
        if isinstance(tool, Toolkit):
            _gate_toolkit(tool, policy)
            gated.append(tool)
        elif isinstance(tool, Function):
            gated.append(_gate_function(tool, policy))
        elif isinstance(tool, dict):
            # A provider-side builtin tool: Alfred never configures any. Drop it rather than
            # let something run that can't be gated.
            logger.warning("dropping provider builtin tool %s: it can't be gated", tool)
        elif callable(tool):
            gated.append(_gate_function(Function.from_callable(tool), policy))
        else:
            logger.warning("dropping unknown tool %r", tool)
    agent.tools = gated
    return agent


def summarise_call(policy: ToolPolicy, tool_name: str, args: Mapping[str, Any] | None) -> str:
    """A human-readable line for one tool call, e.g. "Send a test message to Sam: 'Hi'"."""
    args = dict(args or {})
    found = policy.registry.tool_meta(tool_name)
    template = found[1].confirm_summary if found else None
    if template:
        try:
            return template.format(**args)
        except (KeyError, IndexError, ValueError):
            logger.warning("summary for %s doesn't fit its arguments %s", tool_name, list(args))
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"Run {tool_name} ({shown})" if shown else f"Run {tool_name}"


def join_summaries(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


PolicyLoader = Callable[[], ToolPolicy]
