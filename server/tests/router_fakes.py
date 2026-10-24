"""A fake budget service for router tests."""

from __future__ import annotations

from collections.abc import Iterable

from alfred_server.budget.models import BudgetDecision, DecisionCode, EscalationReason, Tier
from alfred_server.models.gateway import ModelAlias

ALLOW = BudgetDecision(True, "ok", "free", "About £0.01; £19.99 left this month.")


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
