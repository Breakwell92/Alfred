"""Runs eval cases through the router and scores them.

Each case is routed by the real router (classifier on `local-router`, then model selection),
then run on its specialist with every tool in mock mode (`evals/mocks.py`).

Model selection is asked with a budget that allows any cloud alias, so the score shows where
the router *would* send the request (`max_tier`); nothing is spent on that. The run itself never
goes to cloud unless `allow_cloud` is set, and then only when the real budget service allows it:

- `model_alias` set: the specialist runs on that alias alone, never retried on another model, so
  candidates are compared like for like. A cloud alias needs `allow_cloud`, and each case asks
  the budget service first.
- otherwise: the run uses the router's own choice under the run budget (cloud off, or the real
  budget service with `allow_cloud`), cloud fallback included.

Tools are gated as in production (docs/decisions/0013-confirmation-gating.md), with an
in-memory confirmation store: a `write`/`irreversible` call pauses the run, nothing is ever
approved, and the calls it paused for are scored like any other tool call.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, cast

from agno.db.base import AsyncBaseDb, BaseDb
from agno.db.in_memory import InMemoryDb
from agno.models.base import Model
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from alfred_server.agents.context import RequestContext
from alfred_server.agents.registry import Specialist, SpecialistRegistry
from alfred_server.agents.router import (
    EXPECTED_OUTPUT_TOKENS,
    BudgetGate,
    Classification,
    ModelChoice,
    ModelFactory,
    Route,
    Router,
    estimate_prompt_tokens,
)
from alfred_server.budget.models import BudgetDecision, EscalationReason
from alfred_server.confirmations.service import ConfirmationService
from alfred_server.confirmations.store import ConfirmationStore
from alfred_server.connectors.registry import default_registry
from alfred_server.models.gateway import (
    LOCAL_ALIASES,
    MODEL_ALIASES,
    CallMetadata,
    ModelAlias,
    gateway_model,
)
from evals.cases import ANY_TOOL, EvalCase
from evals.mocks import MockTools, ToolCall

# Cloud aliases offered to model selection, in the order the default budget settings prefer.
EVAL_CLOUD_ALIASES: tuple[ModelAlias, ...] = ("cloud-claude", "cloud-gpt", "cloud-gemini")


class AllowAllBudget:
    """Allows every cloud alias: model selection then shows the tier the router wants."""

    def preferred_aliases(self, task_type: str) -> list[ModelAlias]:
        return list(EVAL_CLOUD_ALIASES)

    def decide(
        self,
        alias: ModelAlias,
        prompt_tokens: int,
        expected_output_tokens: int,
        reason: EscalationReason = "auto",
    ) -> BudgetDecision:
        return BudgetDecision(True, "ok", "free", "eval")


class CloudOffBudget:
    """Cloud turned off: runs never fall back to a cloud model."""

    def preferred_aliases(self, task_type: str) -> list[ModelAlias]:
        return []

    def decide(
        self,
        alias: ModelAlias,
        prompt_tokens: int,
        expected_output_tokens: int,
        reason: EscalationReason = "auto",
    ) -> BudgetDecision:
        return BudgetDecision(False, "cloud_disabled", None, "Cloud is off for evals.")


class BudgetRefusedError(RuntimeError):
    """The budget service refused a cloud run."""


class CloudNotAllowedError(ValueError):
    """A cloud `model_alias` was asked for without `allow_cloud`."""


def is_cloud_alias(alias: str) -> bool:
    """A server cloud alias. Other proxy names (`eval-candidate`) are local eval candidates."""
    return alias in MODEL_ALIASES and alias not in LOCAL_ALIASES


def gateway_factory(alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model:
    """`gateway_model`, also for proxy model names that aren't server aliases.

    Evals can compare a candidate the proxy serves under another name (e.g. `eval-candidate`,
    infra/litellm/config.yaml); the server itself only ever uses `MODEL_ALIASES`.
    """
    if alias in MODEL_ALIASES:
        return gateway_model(alias, metadata=metadata, **kwargs)
    model = gateway_model("local-main", metadata=metadata, **kwargs)
    model.id = alias
    model.name = f"LiteLLM {alias}"
    return model


@dataclass
class CaseResult:
    case_id: str
    alias: str  # what the specialist ran on
    specialist: str | None = None
    routed_alias: str | None = None  # where the router would send it with cloud allowed
    routed_tier: str | None = None
    routing_reason: str | None = None
    classification: dict[str, Any] | None = None
    reply: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    route_ms: float = 0.0
    total_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    # check name → passed (None: the case doesn't check it)
    checks: dict[str, bool | None] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.error is None and all(v is not False for v in self.checks.values())


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().casefold() == actual.strip().casefold()
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(k in actual and _matches(v, actual[k]) for k, v in expected.items())
    return bool(expected == actual)


def score(case: EvalCase, result: CaseResult) -> None:
    """Fill `result.checks` and `result.failures` for `case`."""
    checks: dict[str, bool | None] = {}
    failures: list[str] = []

    checks["specialist"] = result.specialist == case.expected_specialist
    if not checks["specialist"]:
        failures.append(f"specialist {result.specialist}, expected {case.expected_specialist}")

    missing = [
        t
        for t in case.expected_tools
        if not any(c.name == t.name and _matches(t.args_subset, c.args) for c in result.tool_calls)
    ]
    checks["tools"] = not missing if case.expected_tools else None
    for t in missing:
        failures.append(f"missing call {t.name}({t.args_subset or ''})")

    if case.must_not_call:
        banned = set(case.must_not_call)
        bad = [c.name for c in result.tool_calls if ANY_TOOL in banned or c.name in banned]
        checks["forbidden"] = not bad
        if bad:
            failures.append(f"forbidden call {', '.join(sorted(set(bad)))}")
    else:
        checks["forbidden"] = None

    checks["tier"] = case.max_tier == "cloud" or result.routed_tier == "local"
    if not checks["tier"]:
        failures.append(f"routed to {result.routed_alias}, max tier {case.max_tier}")

    if case.reply_contains:
        reply = (result.reply or "").casefold()
        absent = [s for s in case.reply_contains if s.casefold() not in reply]
        checks["reply"] = not absent
        if absent:
            failures.append(f"reply lacks {absent}")
    else:
        checks["reply"] = None

    if result.error:
        failures.insert(0, result.error)
    result.checks = checks
    result.failures = failures


def _mocked(specialist: Specialist, tools: MockTools) -> Specialist:
    """`specialist` with every tool call of its agent answered by `tools`."""

    def factory(ctx: RequestContext, model: Model, db: BaseDb | AsyncBaseDb) -> Any:
        agent = specialist.factory(ctx, model, db)
        agent.tool_hooks = [tools, *(agent.tool_hooks or [])]
        return agent

    return replace(specialist, factory=factory)


class EvalRunner:
    """Runs cases on one model alias (or the router's choice when `model_alias` is None)."""

    def __init__(
        self,
        registry: SpecialistRegistry,
        fixtures: dict[str, Any],
        *,
        model_alias: str | None = None,
        allow_cloud: bool = False,
        budget: BudgetGate | None = None,
        model_factory: ModelFactory = gateway_factory,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """`budget` decides real cloud runs when `allow_cloud` is set (default: the budget
        service, so Dan's cap, tiers and providers apply). Raises `CloudNotAllowedError` for a
        cloud `model_alias` without `allow_cloud`.
        """
        if model_alias is not None and is_cloud_alias(model_alias) and not allow_cloud:
            raise CloudNotAllowedError(
                f"--model-alias {model_alias} is a cloud model: add --allow-cloud to spend on it"
            )
        if allow_cloud and budget is None:
            from alfred_server.budget.service import get_budget_service

            budget = get_budget_service()
        self.registry = registry
        self.fixtures = fixtures
        self.model_alias = model_alias
        self.allow_cloud = allow_cloud
        self.budget: BudgetGate = budget if allow_cloud and budget is not None else CloudOffBudget()
        self.model_factory = model_factory
        self.clock = clock
        self.db = InMemoryDb()
        # Records pauses before gated tools, in memory; nothing is ever approved.
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.gate = ConfirmationService(ConfirmationStore(engine), default_registry())
        self._runs = 0

    async def _forced_choice(self, alias: ModelAlias, text: str, c: Classification) -> ModelChoice:
        """`model_alias` as the run's model; a cloud one only if the budget allows it."""
        if not is_cloud_alias(alias):
            return ModelChoice(alias, "eval: --model-alias")
        decision = await asyncio.to_thread(
            self.budget.decide,
            alias,
            estimate_prompt_tokens(text, c),
            EXPECTED_OUTPUT_TOKENS,
            "user_request",
        )
        if not decision.allowed:
            raise BudgetRefusedError(f"budget refused {alias}: {decision.message}")
        return ModelChoice(alias, "eval: --model-alias", decision.code, decision.tier)

    async def run_case(self, case: EvalCase) -> CaseResult:
        self._runs += 1
        ctx = case.request_context(session_id=f"eval-{case.id}-{self._runs}")
        tools = MockTools(self.fixtures, ctx)
        result = CaseResult(case.id, alias=self.model_alias or "router")
        # Runs: cloud off, or the real budget with --allow-cloud. A forced alias is run alone.
        run_budget = CloudOffBudget() if self.model_alias is not None else self.budget
        router = Router(self.registry, run_budget, self.model_factory, self.db, self.gate)
        # Decides only (nothing runs on it): where the router would send the request.
        selector = Router(self.registry, AllowAllBudget(), self.model_factory, self.db)

        start = self.clock()
        try:
            classification = await router.classify(case.input, ctx)
            routed = await selector.select_model(case.input, classification)
            result.route_ms = (self.clock() - start) * 1000
            specialist = self.registry.get(classification.specialist)
            result.specialist = specialist.name
            result.classification = classification.model_dump()
            result.routed_alias, result.routed_tier = routed.alias, routed.tier
            result.routing_reason = routed.reason

            if self.model_alias is not None:
                alias = cast(ModelAlias, self.model_alias)
                choice = await self._forced_choice(alias, case.input, classification)
            else:
                choice = await router.select_model(case.input, classification)
            result.alias = choice.alias
            route = Route(classification, _mocked(specialist, tools), choice)
            reply = await router.run(route, case.input, ctx)
            result.reply = reply.reply
            if reply.confirmation is not None:
                # The gated calls didn't run (no hook call), but the model made them.
                for requirement in reply.run.active_requirements:
                    te = requirement.tool_execution
                    if te is not None and te.tool_name:
                        args = dict(te.tool_args or {})
                        tools.calls.append(
                            ToolCall(te.tool_name, args, te.tool_name in self.fixtures)
                        )
            metrics = reply.run.metrics
            if metrics is not None:
                result.input_tokens = metrics.input_tokens
                result.output_tokens = metrics.output_tokens
        except Exception as e:  # a failed case is scored, not fatal
            result.error = f"{type(e).__name__}: {e}"
        result.total_ms = (self.clock() - start) * 1000
        result.tool_calls = list(tools.calls)
        score(case, result)
        return result
