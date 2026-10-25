"""A fake budget service and confirmation gate for router tests."""

from __future__ import annotations

from collections.abc import Iterable

from agno.run.agent import RunOutput, RunPausedEvent
from agno.tools import Toolkit

from alfred_server.agents.context import RequestContext
from alfred_server.budget.models import (
    BudgetDecision,
    CostEstimate,
    DecisionCode,
    EscalationReason,
    Tier,
)
from alfred_server.confirmations.models import PendingConfirmation
from alfred_server.confirmations.policy import ToolPolicy
from alfred_server.confirmations.service import ConfirmationService
from alfred_server.confirmations.store import ConfirmationStore
from alfred_server.connectors.models import AgnoToolkitSource, Connector, ToolMeta
from alfred_server.connectors.registry import default_registry
from alfred_server.models.gateway import ModelAlias
from tests.budget_helpers import memory_engine

ALLOW = BudgetDecision(True, "ok", "free", "About £0.01; £19.99 left this month.")
# `FakeBudget.estimate_cost` prices every token at this (USD, and GBP at 0.8 per dollar).
USD_PER_TOKEN = 0.00001


def deny(
    code: DecisionCode, message: str = "No.", *, announce: bool = False, tier: Tier | None = None
) -> BudgetDecision:
    return BudgetDecision(False, code, tier, message, announce)


class FakeBudget:
    """`BudgetGate` with canned answers. Records every question it's asked.

    `aliases` is what `preferred_aliases` returns (empty = cloud off); `decisions` maps an alias
    to its `decide` answer (default: allowed).
    """

    def __init__(
        self,
        aliases: Iterable[ModelAlias] = (),
        decisions: dict[ModelAlias, BudgetDecision] | None = None,
    ) -> None:
        self.aliases = list(aliases)
        self.decisions = decisions or {}
        self.task_types: list[str] = []
        self.calls: list[tuple[ModelAlias, EscalationReason]] = []

    def preferred_aliases(self, task_type: str) -> list[ModelAlias]:
        self.task_types.append(task_type)
        return list(self.aliases)

    def decide(
        self,
        alias: ModelAlias,
        prompt_tokens: int,
        expected_output_tokens: int,
        reason: EscalationReason = "auto",
    ) -> BudgetDecision:
        self.calls.append((alias, reason))
        return self.decisions.get(alias, ALLOW)

    def estimate_cost(
        self, alias: ModelAlias, prompt_tokens: int, expected_output_tokens: int
    ) -> CostEstimate:
        usd = (prompt_tokens + expected_output_tokens) * USD_PER_TOKEN
        return CostEstimate(alias, prompt_tokens, expected_output_tokens, usd, usd * 0.8)


def read_only_connector(*tool_names: str) -> Connector:
    """A test connector declaring `tool_names` as `read` tools (so they're never gated)."""
    return Connector(
        id="router_test",
        name="Router test",
        description="Tools the router tests give their agents.",
        auth_type="none",
        tool_source=AgnoToolkitSource(lambda _creds: Toolkit(name="router_test")),
        tools=tuple(ToolMeta(name=n, description=n, risk="read") for n in tool_names),
        specialist="general",
    )


class FakeGate:
    """`ConfirmationGate` over the built-in connectors plus `extra`; keeps what it records."""

    def __init__(self, *extra: Connector) -> None:
        self.registry = default_registry()
        for connector in extra:
            self.registry.register(connector)
        self.created: list[PendingConfirmation] = []

    def policy(self) -> ToolPolicy:
        return ToolPolicy(self.registry)

    def create(
        self,
        run: RunOutput | RunPausedEvent,
        *,
        specialist: str,
        alias: ModelAlias,
        ctx: RequestContext,
        policy: ToolPolicy,
    ) -> PendingConfirmation:
        service = ConfirmationService(ConfirmationStore(memory_engine()), self.registry)
        confirmation = service.create(
            run, specialist=specialist, alias=alias, ctx=ctx, policy=policy
        )
        self.created.append(confirmation)
        return confirmation
