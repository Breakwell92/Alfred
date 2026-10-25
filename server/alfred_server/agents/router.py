"""The router: every request (voice, text, scheduled) enters here.

It classifies the request on `local-router`, picks a model (local first; a cloud alias only when
cloud is enabled, the budget service allows it and the task needs it), hands the request to a
registered specialist on that model, and retries once on cloud if the local model fails.
Rules and why: docs/decisions/0009-router.md.

Tools that change things are gated (docs/decisions/0013-confirmation-gating.md): a run that
reaches one pauses, the gate records it and the reply asks for a yes; `resume` continues it.

Output of `local_only` tools stays on local models unless Dan's privacy settings allow it
(docs/decisions/0026-privacy-routing.md): a run that has such a tool loaded is kept local, and
a cloud call that would carry such output anyway (from history) is refused by the gateway
model and retried locally.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.metrics import RunMetrics
from agno.models.base import Model
from agno.run.agent import (
    RunCompletedEvent,
    RunContentEvent,
    RunErrorEvent,
    RunOutput,
    RunPausedEvent,
)
from agno.run.base import RunStatus
from agno.tools import Toolkit
from agno.tools.function import Function
from agno.tools.mcp import MCPTools
from pydantic import BaseModel, Field

from alfred_server.agents.base import LOW_CONFIDENCE_MARKER
from alfred_server.agents.context import RequestContext
from alfred_server.agents.registry import GENERAL, Specialist, SpecialistRegistry
from alfred_server.budget.models import (
    ALIAS_PROVIDERS,
    PROVIDER_ALIASES,
    BudgetDecision,
    DecisionCode,
    EscalationReason,
    Provider,
    Tier,
)
from alfred_server.budget.service import PROVIDER_NAMES
from alfred_server.confirmations.models import PendingConfirmation
from alfred_server.confirmations.policy import ToolPolicy, gate_agent
from alfred_server.confirmations.service import ask_prompt
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.models.gateway import (
    LOCAL_ALIASES,
    MODEL_ALIASES,
    CallMetadata,
    GatewayModel,
    ModelAlias,
    private_tool_results,
)
from alfred_server.privacy.models import FAIL_CLOSED, PrivacySettings, blocked_tools

logger = logging.getLogger(__name__)

ROUTER_AGENT_NAME = "router"

Complexity = Literal["simple", "standard", "complex"]

# Escalate to cloud when a request needs more tool calls than this.
ESCALATION_TOOL_CALLS = 3
# `simple` requests with at most this many tool calls run on `local-router`.
ROUTER_MODEL_TOOL_CALLS = 1
# A local run with this many invalid tool calls (unknown tool, bad arguments) is retried on cloud.
FALLBACK_INVALID_TOOL_CALLS = 2

# Rough token counts for the budget's pre-call estimate: system prompt and history, each tool
# round trip (the prompt is sent again), a long document, and the reply.
PROMPT_OVERHEAD_TOKENS = 2000
TOKENS_PER_TOOL_CALL = 1500
LONG_CONTEXT_TOKENS = 30000
EXPECTED_OUTPUT_TOKENS = 500

# Said instead of a bare low-confidence marker when cloud isn't available.
NOT_SURE_REPLY = "Sorry, I'm not sure about that."
# Said before the reply when an explicit cloud request was kept local for privacy.
PRIVACY_ANNOUNCEMENT = "That involves private information, so I kept it on the local model."

# Safety net for the classifier: commands that start like this are always explicit cloud
# requests ("Ask Claude to …", "Alfred, use the smart model for this"). Anchored to the start
# and to what follows the name, so questions *about* the products ("How do I use ChatGPT to …",
# "Can I use Claude Code?") don't match.
_EXPLICIT_CLOUD = re.compile(
    r"^\s*(?:alfred,?\s+)?(?:please\s+)?(?:ask|use|have)\s+(?:the\s+)?"
    r"(claude|chat\s?gpt|gpt|gemini|smart(?:er)?\s+model)\b"
    r"(?=\s+(?:to|about|for)\b|\s*[:,.?!]|\s*$)",
    re.IGNORECASE,
)


class Classification(BaseModel):
    """What the classifier decided about a request."""

    specialist: str = GENERAL
    complexity: Complexity = "standard"
    needs_long_context: bool = False
    expected_tool_calls: int = Field(default=1, ge=0, le=20)
    explicit_cloud_request: bool = False
    preferred_provider: Provider | None = None


class _ClassifierOutput(BaseModel):
    """What `local-router` writes. Short keys: every output token costs ~6 ms of latency."""

    agent: str
    level: Complexity
    long: bool
    tools: int = Field(ge=0, le=20)
    cloud: bool
    provider: Literal["claude", "gpt", "gemini", "none"]

    def to_classification(self) -> Classification:
        return Classification(
            specialist=self.agent,
            complexity=self.level,
            needs_long_context=self.long,
            expected_tool_calls=self.tools,
            explicit_cloud_request=self.cloud,
            preferred_provider=None if self.provider == "none" else self.provider,
        )


_CLASSIFIER_PROMPT = """Route a voice assistant request. Reply with JSON only.
agent: which specialist handles it, one of:
{specialists}
level: simple = one quick command or fact; standard = a normal request; complex = multi-step \
reasoning, planning, long writing or analysis.
long: true if it means reading a long document or many emails, messages or pages.
tools: how many tool calls answering will take.
cloud: true only if the user asks for a cloud or smarter model ("ask Claude", "use GPT", \
"use the smart model").
provider: claude, gpt or gemini if the user names one, else none.
Write the JSON on one line with no spaces, e.g. \
{{"agent":"general","level":"simple","long":false,"tools":1,"cloud":false,"provider":"none"}}"""


class ModelFactory(Protocol):
    """Builds the model for `alias` tagged with `metadata`; `kwargs` go to the model."""

    def __call__(self, alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model: ...


class ToolLoader(Protocol):
    """Connector tools for a specialist (`ConnectorService.load_tools`), loaded per request."""

    async def __call__(self, specialist: str) -> Sequence[Toolkit | MCPTools]: ...


class BudgetGate(Protocol):
    """The parts of `BudgetService` the router uses (sync: they read Postgres)."""

    def preferred_aliases(self, task_type: str) -> list[ModelAlias]: ...

    def decide(
        self,
        alias: ModelAlias,
        prompt_tokens: int,
        expected_output_tokens: int,
        reason: EscalationReason = "auto",
    ) -> BudgetDecision: ...


class ConfirmationGate(Protocol):
    """The parts of `ConfirmationService` the router uses (sync: they read Postgres)."""

    def policy(self) -> ToolPolicy: ...

    def create(
        self,
        run: RunOutput | RunPausedEvent,
        *,
        specialist: str,
        alias: ModelAlias,
        ctx: RequestContext,
        policy: ToolPolicy,
    ) -> PendingConfirmation: ...


class PrivacyGate(Protocol):
    """The part of `PrivacyService` the router uses (sync: it reads Postgres)."""

    def settings(self) -> PrivacySettings: ...


@dataclass(frozen=True)
class ModelChoice:
    """The model a request runs on and why. `announcement` is said before the reply.

    `private` names the connectors whose `local_only` tools (not allowed to cloud) the run
    has loaded or carries output from; such a run never uses a cloud model. `downgraded_from`
    is the cloud alias it would have used otherwise (reason `privacy`).
    """

    alias: ModelAlias
    reason: str
    budget_code: DecisionCode | None = None
    budget_tier: Tier | None = None
    announcement: str | None = None
    private: tuple[str, ...] = ()
    downgraded_from: ModelAlias | None = None

    @property
    def tier(self) -> Literal["local", "cloud"]:
        return "local" if self.alias in LOCAL_ALIASES else "cloud"

    def metadata(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "alias": self.alias,
            "tier": self.tier,
            "reason": self.reason,
            "budget_code": self.budget_code,
            "budget_tier": self.budget_tier,
        }
        if self.private:
            meta["privacy"] = {
                "connectors": list(self.private),
                "downgraded_from": self.downgraded_from,
            }
        return meta


@dataclass(frozen=True)
class Route:
    classification: Classification
    specialist: Specialist
    choice: ModelChoice


@dataclass(frozen=True)
class RoutedReply:
    """A finished request: the text to send (any announcement included) and how it ran."""

    reply: str
    route: Route
    run: RunOutput
    # Set when the run paused before a gated tool: `reply` then asks for the OK.
    confirmation: PendingConfirmation | None = None


@dataclass(frozen=True)
class Token:
    """Streamed reply text."""

    text: str


@dataclass(frozen=True)
class Done:
    """The stream finished; `reply` is the whole reply, announcement included.

    `tools` names the tool calls the final run made, `metrics` its token counts (if any).
    `confirmation` is set when the run paused before a gated tool. `run_id` is the Agno run
    the reply came from, if known.
    """

    reply: str
    route: Route
    tools: tuple[str, ...] = ()
    metrics: RunMetrics | None = None
    confirmation: PendingConfirmation | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class Failed:
    """The run failed; nothing more follows."""


class AgentRunError(RuntimeError):
    """The specialist's run failed (model error or no text reply)."""


def task_type(c: Classification) -> str:
    """Budget provider-preference key for the request."""
    if c.needs_long_context:
        return "long_document"
    if c.expected_tool_calls > ESCALATION_TOOL_CALLS:
        return "multi_step_tools"
    return "default"


def estimate_prompt_tokens(text: str, c: Classification) -> int:
    tokens = PROMPT_OVERHEAD_TOKENS + len(text) // 4
    tokens += TOKENS_PER_TOOL_CALL * c.expected_tool_calls
    if c.needs_long_context:
        tokens += LONG_CONTEXT_TOKENS
    return tokens


def _local_choice(c: Classification) -> tuple[ModelAlias, str]:
    if c.complexity == "simple" and c.expected_tool_calls <= ROUTER_MODEL_TOOL_CALLS:
        return "local-router", "simple"
    return "local-main", c.complexity


def _escalation(c: Classification) -> tuple[EscalationReason, str] | None:
    """Why this request wants a cloud model (budget reason, our reason), or None."""
    if c.explicit_cloud_request:
        return "user_request", "explicit_request"
    if c.complexity == "complex":
        return "hard_task", "complex"
    if c.expected_tool_calls > ESCALATION_TOOL_CALLS:
        return "auto", "many_tool_calls"
    if c.needs_long_context:
        return "auto", "long_context"
    return None


def _explicit_provider(text: str) -> Provider | None | Literal[False]:
    """The provider an explicit cloud phrase names, None if it names none, False if no phrase."""
    match = _EXPLICIT_CLOUD.search(text)
    if match is None:
        return False
    name = match.group(1).lower().replace(" ", "")
    if name == "claude":
        return "claude"
    if name in ("gpt", "chatgpt"):
        return "gpt"
    if name == "gemini":
        return "gemini"
    return None


def _is_marker(text: str) -> bool:
    return text.strip() == LOW_CONFIDENCE_MARKER


def _could_become_marker(text: str) -> bool:
    return LOW_CONFIDENCE_MARKER.startswith(text.strip())


def _invalid_tool_calls(run: RunOutput) -> int:
    """Tool calls in this run that Agno couldn't start: unknown tool or invalid arguments.

    Those leave only a tool message saying so, with no `ToolExecution`. A tool that ran and
    raised (Home Assistant offline, expired token) is not the model's fault and isn't counted.
    """
    executed = {t.tool_call_id for t in run.tools or []}
    return sum(
        1
        for m in run.messages or []
        if m.role == "tool" and not m.from_history and m.tool_call_id not in executed
    )


def _needs_fallback(run: RunOutput) -> bool:
    """Should this local run be retried on cloud?

    Only when the model itself failed (the low-confidence marker, or repeated invalid tool
    calls), and never once a tool has done something: a retry would repeat that action, and
    deleting the run would lose its record.
    """
    if run.status in (RunStatus.error, RunStatus.paused):
        return False
    if any(not t.tool_call_error for t in run.tools or []):
        return False
    marker = isinstance(run.content, str) and _is_marker(run.content)
    return marker or _invalid_tool_calls(run) >= FALLBACK_INVALID_TOOL_CALLS


def _read_only(tools: list[Toolkit | MCPTools], policy: ToolPolicy) -> list[Toolkit | MCPTools]:
    """Only the tools whose declared risk is `read`.

    By risk, not tier: a `write` tool that runs without asking (lights, music) is still
    dropped. MCP toolkits are shared across requests, so they're dropped whole rather than
    trimmed. Agno toolkits are built per load, so trimming them affects only this request.
    """
    kept: list[Toolkit | MCPTools] = []
    for toolkit in tools:
        if isinstance(toolkit, MCPTools):
            logger.warning("not giving MCP toolkit %s read-only: dropped", toolkit.name)
            continue
        for registry in (toolkit.functions, toolkit.async_functions):
            for name in list(registry):
                found = policy.registry.tool_meta(name)
                if found is None or found[1].risk != "read":
                    del registry[name]
        kept.append(toolkit)
    return kept


def _with_announcement(announcement: str | None, reply: str) -> str:
    return f"{announcement} {reply}" if announcement else reply


def _connectors_of(tool_names: Iterable[str], registry: ConnectorRegistry) -> tuple[str, ...]:
    return tuple(
        sorted({found[0].id for n in tool_names if (found := registry.tool_meta(n)) is not None})
    )


def _private_connectors(
    tools: Sequence[Any], registry: ConnectorRegistry, blocked: frozenset[str]
) -> tuple[str, ...]:
    """Connectors with a tool in `blocked` among `tools` (an agent's tools, after gating).

    An MCP toolkit counts with every tool its connector declares, in case it registers more
    once the server is asked.
    """
    if not blocked:
        return ()
    names: set[str] = set()
    for tool in tools:
        if isinstance(tool, Toolkit):
            names.update(tool.functions)
            names.update(tool.async_functions)
            if isinstance(tool, MCPTools) and (connector := registry.get(tool.name)) is not None:
                names.update(connector.tool_names)
        elif isinstance(tool, Function):
            names.add(tool.name)
    return _connectors_of(names & blocked, registry)


def _kept_local(choice: ModelChoice, c: Classification, private: tuple[str, ...]) -> ModelChoice:
    """`choice` for a run that holds private output of `private`'s connectors: local only."""
    private = tuple(sorted({*choice.private, *private}))
    if choice.tier == "local":
        return replace(choice, private=private)
    alias, _ = _local_choice(c)
    logger.info("kept on %s instead of %s for privacy (%s)", alias, choice.alias, private)
    return ModelChoice(
        alias,
        "privacy",
        announcement=PRIVACY_ANNOUNCEMENT if c.explicit_cloud_request else None,
        private=private,
        downgraded_from=choice.alias,
    )


def _refused(agent: Agent) -> tuple[str, ...]:
    """Tools whose private output the agent's gateway model refused to send to the cloud."""
    return agent.model.refused_tools if isinstance(agent.model, GatewayModel) else ()


def _ran_tools(run: RunOutput | None) -> bool:
    """Did a tool do something in this run? None (unreadable) counts as yes."""
    return run is None or any(not t.tool_call_error for t in run.tools or [])


class Router:
    """Routes one request. Cheap to build: built per request like the agents."""

    def __init__(
        self,
        registry: SpecialistRegistry,
        budget: BudgetGate,
        model_factory: ModelFactory,
        db: BaseDb | AsyncBaseDb,
        gate: ConfirmationGate | None = None,
        tools: ToolLoader | None = None,
        privacy: PrivacyGate | None = None,
    ) -> None:
        self.registry = registry
        self.budget = budget
        self.model_factory = model_factory
        self.db = db
        # Without a gate (some tests) tools are still gated by their declared risk, but a run
        # that pauses can't be recorded, so it fails.
        self.gate = gate
        # Loads the tools of specialists with `uses_connectors`. Without one (tests) they
        # run with only their own tools.
        self.tools = tools
        # Dan's privacy settings. Without them (tests, evals) private output may go to cloud,
        # the settings' default.
        self.privacy = privacy

    # --- Classification --------------------------------------------------------------------

    async def classify(self, text: str, ctx: RequestContext) -> Classification:
        """Classify `text` on `local-router`. Falls back to the default if the model fails.

        Raises `GatewayNotConfiguredError` (from the model factory) when no model can be built.
        """
        model = self.model_factory(
            "local-router",
            CallMetadata(agent=ROUTER_AGENT_NAME, trigger=ctx.device_type),
            max_tokens=80,
            temperature=0,
        )
        agent = Agent(
            name=ROUTER_AGENT_NAME,
            model=model,
            instructions=_CLASSIFIER_PROMPT.format(specialists=self.registry.describe()),
            output_schema=_ClassifierOutput,
            markdown=False,
            telemetry=False,
        )
        try:
            run = await agent.arun(text)
        except Exception:
            logger.exception("classifier failed; using the default route")
            run = None
        if run is not None and isinstance(run.content, _ClassifierOutput):
            classification = run.content.to_classification()
        else:
            if run is not None:
                logger.warning("classifier gave no valid output (%s)", run.status)
            classification = Classification()

        if classification.specialist not in self.registry.names():
            classification.specialist = GENERAL
        provider = _explicit_provider(text)
        if provider is not False:
            classification.explicit_cloud_request = True
            classification.preferred_provider = provider or classification.preferred_provider
        return classification

    # --- Model selection -------------------------------------------------------------------

    async def _first_allowed(
        self, aliases: Sequence[ModelAlias], prompt_tokens: int, reason: EscalationReason
    ) -> tuple[ModelAlias | None, BudgetDecision | None]:
        """The first alias the budget allows, else None and the last refusal.

        An alias with no price (not configured on the proxy) moves on to the next one; any
        other refusal (budget, tier, spend unreadable) would refuse them all, so it stops.
        """
        decision = None
        for alias in aliases:
            decision = await asyncio.to_thread(
                self.budget.decide, alias, prompt_tokens, EXPECTED_OUTPUT_TOKENS, reason
            )
            if decision.allowed:
                return alias, decision
            if decision.code != "price_unavailable":
                break
        return None, decision

    async def select_model(self, text: str, c: Classification) -> ModelChoice:
        local_alias, local_reason = _local_choice(c)
        escalation = _escalation(c)
        if escalation is None:
            return ModelChoice(local_alias, local_reason)
        reason, why = escalation

        candidates = await asyncio.to_thread(self.budget.preferred_aliases, task_type(c))
        explicit = reason == "user_request"
        # An explicit request for a provider that is turned off is told so, then served by the
        # next preferred one.
        unavailable: str | None = None
        if explicit:
            wanted = PROVIDER_ALIASES[c.preferred_provider] if c.preferred_provider else None
            if wanted in candidates:
                candidates = [wanted, *(a for a in candidates if a != wanted)]
            elif not candidates:
                # Cloud is off (or the settings can't be read): ask anyway, for the reason to say.
                candidates = [wanted or PROVIDER_ALIASES["claude"]]
            elif c.preferred_provider is not None:
                unavailable = f"{PROVIDER_NAMES[c.preferred_provider]} is turned off"
        if not candidates:
            return ModelChoice(local_alias, f"{why}; cloud unavailable")

        alias, decision = await self._first_allowed(
            candidates, estimate_prompt_tokens(text, c), reason
        )
        if alias is not None and decision is not None:
            used = None
            if unavailable:
                used = f"{unavailable}, so I asked {PROVIDER_NAMES[ALIAS_PROVIDERS[alias]]}."
            return ModelChoice(alias, why, decision.code, decision.tier, used)
        assert decision is not None  # candidates was not empty
        # An explicit request is always told why it stayed local; otherwise only the
        # once-a-month over-budget announcement is said.
        announcement = decision.message if (explicit or decision.announce) else None
        if announcement and unavailable:
            announcement = f"{unavailable}. {announcement}"
        return ModelChoice(
            local_alias,
            f"{why}; budget refused",
            decision.code,
            decision.tier,
            announcement,
        )

    async def route(self, text: str, ctx: RequestContext) -> Route:
        classification = await self.classify(text, ctx)
        choice = await self.select_model(text, classification)
        specialist = self.registry.get(classification.specialist)
        logger.info("routed to %s on %s (%s)", specialist.name, choice.alias, choice.reason)
        return Route(classification, specialist, choice)

    async def _fallback(self, route: Route, text: str) -> tuple[ModelChoice | None, str | None]:
        """A cloud model to retry a failed local run on, and anything to say if there's none."""
        if route.choice.tier == "cloud" or route.choice.private:
            return None, None
        c = route.classification
        candidates = await asyncio.to_thread(self.budget.preferred_aliases, task_type(c))
        if not candidates:
            return None, None
        # The local model has just failed, so this counts as a hard task in the restricted tier.
        alias, decision = await self._first_allowed(
            candidates, estimate_prompt_tokens(text, c), "hard_task"
        )
        if alias is not None and decision is not None:
            reason = f"fallback from {route.choice.alias}"
            return ModelChoice(alias, reason, decision.code, decision.tier), None
        return None, decision.message if decision is not None and decision.announce else None

    # --- Running -----------------------------------------------------------------------------

    async def _policy(self) -> ToolPolicy:
        """Tool tiers for this request. Raises if the overrides can't be read (fail closed)."""
        if self.gate is None:
            return ToolPolicy(default_registry())
        return await asyncio.to_thread(self.gate.policy)

    async def _blocked_tools(self, policy: ToolPolicy) -> frozenset[str]:
        """`local_only` tools whose output must stay local for this request.

        If the settings can't be read, every `local_only` tool's (fail closed).
        """
        if self.privacy is None:
            return frozenset()
        try:
            settings = await asyncio.to_thread(self.privacy.settings)
        except Exception:
            logger.exception("privacy settings unreadable; keeping private tool output local")
            settings = FAIL_CLOSED
        return blocked_tools(policy.registry, settings)

    async def _connector_tools(
        self, specialist: Specialist, policy: ToolPolicy
    ) -> list[Toolkit | MCPTools]:
        """The specialist's connector tools, and the `read` tools of its `read_only_sources`.

        A loading failure leaves it without them (it says the service isn't connected) rather
        than failing the request.
        """
        if not specialist.uses_connectors or self.tools is None:
            return []
        loaded: list[Toolkit | MCPTools] = []
        for name in (specialist.name, *specialist.read_only_sources):
            try:
                tools = list(await self.tools(name))
            except Exception:
                logger.exception("couldn't load %s connector tools for %s", name, specialist.name)
                continue
            if name != specialist.name:
                tools = _read_only(tools, policy)
            loaded.extend(tools)
        return loaded

    async def _agent(
        self,
        route: Route,
        choice: ModelChoice,
        ctx: RequestContext,
        policy: ToolPolicy,
        blocked: frozenset[str],
    ) -> tuple[Agent, ModelChoice]:
        """The specialist's agent for `choice`, and the choice it actually runs on.

        That is a local alias whenever the agent has a tool in `blocked` loaded.
        """
        model = self.model_factory(choice.alias, self._call_metadata(route, ctx))
        agent = route.specialist.factory(ctx, model, self.db)
        extra = await self._connector_tools(route.specialist, policy)
        if extra:
            own = agent.tools if isinstance(agent.tools, list) else []
            agent.tools = [*own, *extra]
        gate_agent(agent, policy)
        tools = agent.tools if isinstance(agent.tools, list) else []
        private = _private_connectors(tools, policy.registry, blocked)
        if private:
            kept = _kept_local(choice, route.classification, private)
            if kept.alias != choice.alias:
                agent.model = self.model_factory(kept.alias, self._call_metadata(route, ctx))
            choice = kept
        self._guard(agent, blocked)
        return agent, choice

    @staticmethod
    def _call_metadata(route: Route, ctx: RequestContext) -> CallMetadata:
        return CallMetadata(agent=route.specialist.name, trigger=ctx.device_type)

    @staticmethod
    def _guard(agent: Agent, blocked: frozenset[str]) -> None:
        """Arm the gateway model's backstop: it refuses cloud calls carrying `blocked` output."""
        if isinstance(agent.model, GatewayModel):
            agent.model.private_tools = blocked

    async def _paused(
        self,
        run: RunOutput | RunPausedEvent,
        route: Route,
        ctx: RequestContext,
        policy: ToolPolicy,
    ) -> PendingConfirmation:
        """Record a run that paused before a gated tool. Raises `AgentRunError` if it can't."""
        gate = self.gate
        if gate is None:
            raise AgentRunError("a run paused for confirmation but there is no gate")
        try:
            return await asyncio.to_thread(
                lambda: gate.create(
                    run,
                    specialist=route.specialist.name,
                    alias=route.choice.alias,
                    ctx=ctx,
                    policy=policy,
                )
            )
        except Exception as exc:
            raise AgentRunError(f"couldn't record the confirmation: {exc}") from exc

    @staticmethod
    def _run_metadata(route: Route, choice: ModelChoice) -> dict[str, Any]:
        """Stored on the Agno run, for the action log."""
        return {
            "routing": {
                **choice.metadata(),
                "specialist": route.specialist.name,
                "classification": route.classification.model_dump(),
            }
        }

    async def _forget_run(self, run_id: str | None) -> None:
        """Delete a failed run so the retry's history doesn't hold the question twice.

        If that fails the retry still goes ahead, with the failed attempt in its history.
        """
        if run_id is None:
            return
        try:
            if isinstance(self.db, AsyncBaseDb):
                deleted = await self.db.delete_run(run_id)
            else:
                deleted = await asyncio.to_thread(self.db.delete_run, run_id)
        except Exception:
            logger.exception("couldn't delete failed run %s", run_id)
            return
        if not deleted:
            logger.warning("failed run %s wasn't deleted; the retry will see it in history", run_id)

    async def run(self, route: Route, text: str, ctx: RequestContext) -> RoutedReply:
        """Run the request to completion. Raises `AgentRunError` if the run fails."""
        policy = await self._policy()
        blocked = await self._blocked_tools(policy)
        run, route = await self._attempt(route, text, ctx, policy, blocked)
        announcement = route.choice.announcement
        if _needs_fallback(run):
            fallback, said = await self._fallback(route, text)
            announcement = announcement or said
            if fallback is not None:
                logger.info("retrying on %s (%s)", fallback.alias, fallback.reason)
                await self._forget_run(run.run_id)
                route = Route(route.classification, route.specialist, fallback)
                run, route = await self._attempt(route, text, ctx, policy, blocked)
                announcement = announcement or route.choice.announcement
        return await self._finish(run, route, ctx, policy, announcement)

    async def _attempt(
        self,
        route: Route,
        text: str,
        ctx: RequestContext,
        policy: ToolPolicy,
        blocked: frozenset[str],
    ) -> tuple[RunOutput, Route]:
        """One run on `route.choice` (kept local for privacy if need be).

        If the gateway model refused a cloud call for carrying private output (from history),
        the run is retried once locally, unless a tool already did something.
        """
        agent, choice = await self._agent(route, route.choice, ctx, policy, blocked)
        route = replace(route, choice=choice)
        run = await agent.arun(
            text, session_id=ctx.session_id, metadata=self._run_metadata(route, choice)
        )
        refused = _refused(agent)
        if not refused or _ran_tools(run):
            return run, route
        logger.warning("cloud call refused: it held private output of %s", ", ".join(refused))
        await self._forget_run(run.run_id)
        kept = _kept_local(choice, route.classification, _connectors_of(refused, policy.registry))
        agent, choice = await self._agent(replace(route, choice=kept), kept, ctx, policy, blocked)
        route = replace(route, choice=choice)
        run = await agent.arun(
            text, session_id=ctx.session_id, metadata=self._run_metadata(route, choice)
        )
        return run, route

    async def _finish(
        self,
        run: RunOutput,
        route: Route,
        ctx: RequestContext,
        policy: ToolPolicy,
        announcement: str | None,
    ) -> RoutedReply:
        if run.status == RunStatus.paused:
            confirmation = await self._paused(run, route, ctx, policy)
            reply = _with_announcement(announcement, ask_prompt(confirmation))
            return RoutedReply(reply, route, run, confirmation)
        if run.status == RunStatus.error or not isinstance(run.content, str):
            raise AgentRunError(f"agent run failed: {run.content}")
        reply = NOT_SURE_REPLY if _is_marker(run.content) else run.content
        return RoutedReply(_with_announcement(announcement, reply), route, run)

    async def stream(
        self, route: Route, text: str, ctx: RequestContext
    ) -> AsyncIterator[Token | Done | Failed]:
        """Stream the reply as `Token`s, then `Done` (or `Failed`).

        Text that could still be the low-confidence marker is held back until it can't be, so
        a marker reply can be retried on cloud before anything reaches the user. A run that
        has already sent text is never retried.
        """
        policy = await self._policy()
        blocked = await self._blocked_tools(policy)
        attempt = _StreamAttempt(policy, blocked)
        async for item in attempt.run(self, route, text, ctx, announce=True):
            yield item
        if attempt.failed:
            yield Failed()
            return
        route = attempt.route or route

        announcement = attempt.announced
        if not attempt.sent and await attempt.needs_fallback():
            fallback, said = await self._fallback(route, text)
            if said and not announcement:
                announcement = said
                yield Token(said + " ")
            if fallback is not None:
                logger.info("retrying on %s (%s)", fallback.alias, fallback.reason)
                await self._forget_run(attempt.run_id)
                route = Route(route.classification, route.specialist, fallback)
                attempt = _StreamAttempt(policy, blocked)
                async for item in attempt.run(self, route, text, ctx, announce=not announcement):
                    yield item
                if attempt.failed:
                    yield Failed()
                    return
                route = attempt.route or route
                announcement = announcement or attempt.announced

        if attempt.paused is not None:
            try:
                confirmation = await self._paused(attempt.paused, route, ctx, policy)
            except AgentRunError:
                logger.exception("stream paused but the confirmation wasn't recorded")
                yield Failed()
                return
            # Text held back before the tool call is dropped; the prompt is what to answer.
            prompt = ask_prompt(confirmation)
            yield Token((" " if attempt.sent else "") + prompt)
            said = "".join(attempt.parts) + " " + prompt if attempt.sent else prompt
            reply = _with_announcement(announcement, said)
            # Only tools that ran before the pause; the waiting ones are `confirmation.tools`.
            ran = tuple(
                t.tool_name
                for t in attempt.paused.tools or []
                if t.tool_name and t.result is not None and not t.requires_confirmation
            )
            yield Done(reply, route, ran, attempt.metrics, confirmation, attempt.run_id)
            return

        reply = attempt.reply()
        if not attempt.sent and reply:  # still held back: the marker, or a very short reply
            yield Token(reply)
        run = await attempt.stored_run()
        tools = tuple(t.tool_name for t in (run.tools if run else None) or [] if t.tool_name)
        metrics = run.metrics if run is not None and run.metrics is not None else attempt.metrics
        yield Done(
            _with_announcement(announcement, reply), route, tools, metrics, run_id=attempt.run_id
        )

    # --- Confirmations -------------------------------------------------------------------------

    async def resume(self, confirmation: PendingConfirmation, ctx: RequestContext) -> RoutedReply:
        """Continue a run the user approved: the gated tools run, then the model replies.

        The caller has already claimed `confirmation` (so it can't be resumed twice). The run
        continues on the alias it paused on; a cloud alias asks the budget again first and
        drops to `local-main` if refused. If the continued run reaches another gated tool it
        pauses again and the reply asks for that one. Raises `AgentRunError` on failure.
        """
        specialist = self.registry.get(confirmation.specialist)
        alias: ModelAlias = (
            cast(ModelAlias, confirmation.alias)
            if confirmation.alias in MODEL_ALIASES
            else "local-main"
        )
        choice = ModelChoice(alias, "resume after confirmation")
        if alias not in LOCAL_ALIASES:
            decision = await asyncio.to_thread(
                self.budget.decide,
                alias,
                PROMPT_OVERHEAD_TOKENS,
                EXPECTED_OUTPUT_TOKENS,
                "auto",
            )
            if decision.allowed:
                choice = ModelChoice(alias, choice.reason, decision.code, decision.tier)
            else:
                choice = ModelChoice(
                    "local-main", "resume after confirmation; budget refused", decision.code
                )
        route = Route(Classification(specialist=specialist.name), specialist, choice)
        policy = await self._policy()
        blocked = await self._blocked_tools(policy)
        agent, choice = await self._agent(route, choice, ctx, policy, blocked)
        route = replace(route, choice=choice)
        stored = await agent.aget_run_output(
            confirmation.run_id, session_id=confirmation.session_id
        )
        if not isinstance(stored, RunOutput) or stored.status != RunStatus.paused:
            raise AgentRunError(f"run {confirmation.run_id} isn't paused any more")
        # Output a private tool returned before the pause stays local too.
        found = private_tool_results(stored.messages or [], blocked)
        if found and choice.tier == "cloud":
            private = _connectors_of(found, policy.registry)
            choice = _kept_local(choice, route.classification, private)
            route = replace(route, choice=choice)
            agent.model = self.model_factory(choice.alias, self._call_metadata(route, ctx))
            self._guard(agent, blocked)
        requirements = stored.active_requirements
        for requirement in requirements:
            if requirement.needs_confirmation:
                requirement.confirm()
        run = await agent.acontinue_run(
            run_id=confirmation.run_id,
            requirements=requirements,
            session_id=confirmation.session_id,
            metadata=self._run_metadata(route, choice),
        )
        return await self._finish(run, route, ctx, policy, None)


class _StreamAttempt:
    """One streamed run of the specialist, with the marker held back.

    Like `Router._attempt`, a run whose cloud call the gateway model refused for privacy is
    retried once locally, if nothing was sent and no tool did anything yet.
    """

    def __init__(self, policy: ToolPolicy, blocked: frozenset[str]) -> None:
        self.policy = policy
        self.blocked = blocked
        # The route the run actually used (its choice may have been kept local), once known.
        self.route: Route | None = None
        # The announcement this attempt said before its reply, if any.
        self.announced: str | None = None
        self._reset()

    def _reset(self) -> None:
        self.paused: RunPausedEvent | None = None
        self.held = ""  # text not sent yet because it could still be the marker
        self.sent = False
        self.parts: list[str] = []
        self.completed: str | None = None
        self.metrics: RunMetrics | None = None
        self.failed = False
        self.run_id: str | None = None
        self.session_id: str | None = None
        self.agent: Agent | None = None
        self._stored: RunOutput | None = None

    async def run(
        self, router: Router, route: Route, text: str, ctx: RequestContext, *, announce: bool
    ) -> AsyncIterator[Token]:
        """Stream the run, saying the choice's announcement first if `announce`."""
        agent, choice = await router._agent(route, route.choice, ctx, self.policy, self.blocked)
        self.route = route = replace(route, choice=choice)
        if announce and choice.announcement:
            self.announced = choice.announcement
            yield Token(choice.announcement + " ")
        async for token in self._stream(router, agent, route, text, ctx):
            yield token
        refused = _refused(agent)
        if not refused or self.sent or self.paused is not None:
            return
        if _ran_tools(await self.stored_run()):
            return
        logger.warning("cloud call refused: it held private output of %s", ", ".join(refused))
        await router._forget_run(self.run_id)
        kept = _kept_local(
            choice, route.classification, _connectors_of(refused, self.policy.registry)
        )
        agent, choice = await router._agent(
            replace(route, choice=kept), kept, ctx, self.policy, self.blocked
        )
        self.route = route = replace(route, choice=choice)
        self._reset()
        if announce and self.announced is None and choice.announcement:
            self.announced = choice.announcement
            yield Token(choice.announcement + " ")
        async for token in self._stream(router, agent, route, text, ctx):
            yield token

    async def _stream(
        self, router: Router, agent: Agent, route: Route, text: str, ctx: RequestContext
    ) -> AsyncIterator[Token]:
        choice = route.choice
        self.agent = agent
        self.session_id = ctx.session_id
        try:
            async for event in agent.arun(
                text,
                session_id=ctx.session_id,
                stream=True,
                metadata=router._run_metadata(route, choice),
            ):
                if self.run_id is None:
                    self.run_id = getattr(event, "run_id", None)
                if isinstance(event, RunContentEvent) and isinstance(event.content, str):
                    if not event.content:
                        continue
                    self.parts.append(event.content)
                    if self.sent:
                        yield Token(event.content)
                    else:
                        self.held += event.content
                        if not _could_become_marker(self.held):
                            self.sent = True
                            yield Token(self.held)
                elif isinstance(event, RunErrorEvent):
                    logger.error(
                        "stream run failed (session %s): %s", ctx.session_id, event.content
                    )
                    self.failed = True
                    return
                elif isinstance(event, RunPausedEvent):
                    self.paused = event
                elif isinstance(event, RunCompletedEvent):
                    self.metrics = event.metrics
                    if isinstance(event.content, str):
                        # The completed run's content is authoritative (deltas may skip tool
                        # output).
                        self.completed = event.content
        except Exception:
            logger.exception("stream run failed (session %s)", ctx.session_id)
            self.failed = True

    async def stored_run(self) -> RunOutput | None:
        """The finished run as stored: stream events miss its tool calls.

        None if it can't be read. By then the reply may already have reached the user, and the
        stored run only adds metadata, so a storage error mustn't fail the stream.
        """
        if self._stored is None and self.agent is not None and self.run_id is not None:
            try:
                run = await self.agent.aget_run_output(self.run_id, session_id=self.session_id)
            except Exception:
                logger.warning("couldn't read stored run %s", self.run_id, exc_info=True)
                return None
            self._stored = run if isinstance(run, RunOutput) else None
        return self._stored

    async def needs_fallback(self) -> bool:
        """`_needs_fallback` on the stored run (stream events miss invalid tool calls)."""
        if self.paused is not None:
            return False
        run = await self.stored_run()
        if run is None:  # can't check its tools: only a bare marker counts
            return _is_marker(self.held)
        return _needs_fallback(run)

    def reply(self) -> str:
        reply = self.completed if self.completed is not None else "".join(self.parts)
        return NOT_SURE_REPLY if _is_marker(reply) else reply
