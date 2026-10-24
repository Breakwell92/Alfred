"""The router: every request (voice, text, scheduled) enters here.

It classifies the request on `local-router`, picks a model (local first; a cloud alias only when
cloud is enabled, the budget service allows it and the task needs it), hands the request to a
registered specialist on that model, and retries once on cloud if the local model fails.
Rules and why: docs/decisions/0009-router.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.metrics import RunMetrics
from agno.models.base import Model
from agno.run.agent import (
    RunCompletedEvent,
    RunContentEvent,
    RunErrorEvent,
    RunOutput,
)
from agno.run.base import RunStatus
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
from alfred_server.models.gateway import LOCAL_ALIASES, CallMetadata, ModelAlias

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


@dataclass(frozen=True)
class ModelChoice:
    """The model a request runs on and why. `announcement` is said before the reply."""

    alias: ModelAlias
    reason: str
    budget_code: DecisionCode | None = None
    budget_tier: Tier | None = None
    announcement: str | None = None

    @property
    def tier(self) -> Literal["local", "cloud"]:
        return "local" if self.alias in LOCAL_ALIASES else "cloud"

    def metadata(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "tier": self.tier,
            "reason": self.reason,
            "budget_code": self.budget_code,
            "budget_tier": self.budget_tier,
        }


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


@dataclass(frozen=True)
class Token:
    """Streamed reply text."""

    text: str


@dataclass(frozen=True)
class Done:
    """The stream finished; `reply` is the whole reply, announcement included.

    `tools` names the tool calls the final run made, `metrics` its token counts (if any).
    """

    reply: str
    route: Route
    tools: tuple[str, ...] = ()
    metrics: RunMetrics | None = None


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
    if run.status == RunStatus.error:
        return False
    if any(not t.tool_call_error for t in run.tools or []):
        return False
    marker = isinstance(run.content, str) and _is_marker(run.content)
    return marker or _invalid_tool_calls(run) >= FALLBACK_INVALID_TOOL_CALLS


def _with_announcement(announcement: str | None, reply: str) -> str:
    return f"{announcement} {reply}" if announcement else reply


class Router:
    """Routes one request. Cheap to build: built per request like the agents."""

    def __init__(
        self,
        registry: SpecialistRegistry,
        budget: BudgetGate,
        model_factory: ModelFactory,
        db: BaseDb | AsyncBaseDb,
    ) -> None:
        self.registry = registry
        self.budget = budget
        self.model_factory = model_factory
        self.db = db

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
        if route.choice.tier == "cloud":
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

    def _agent(self, route: Route, choice: ModelChoice, ctx: RequestContext) -> Agent:
        metadata = CallMetadata(agent=route.specialist.name, trigger=ctx.device_type)
        model = self.model_factory(choice.alias, metadata)
        return route.specialist.factory(ctx, model, self.db)

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
        choice = route.choice
        agent = self._agent(route, choice, ctx)
        run = await agent.arun(
            text, session_id=ctx.session_id, metadata=self._run_metadata(route, choice)
        )
        announcement = choice.announcement
        if _needs_fallback(run):
            fallback, said = await self._fallback(route, text)
            announcement = announcement or said
            if fallback is not None:
                logger.info("retrying on %s (%s)", fallback.alias, fallback.reason)
                await self._forget_run(run.run_id)
                route = Route(route.classification, route.specialist, fallback)
                agent = self._agent(route, fallback, ctx)
                run = await agent.arun(
                    text, session_id=ctx.session_id, metadata=self._run_metadata(route, fallback)
                )
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
        if route.choice.announcement:
            yield Token(route.choice.announcement + " ")
        attempt = _StreamAttempt()
        async for item in attempt.run(self, route, route.choice, text, ctx):
            yield item
        if attempt.failed:
            yield Failed()
            return

        announcement = route.choice.announcement
        if not attempt.sent and await attempt.needs_fallback():
            fallback, said = await self._fallback(route, text)
            if said and not announcement:
                announcement = said
                yield Token(said + " ")
            if fallback is not None:
                logger.info("retrying on %s (%s)", fallback.alias, fallback.reason)
                await self._forget_run(attempt.run_id)
                route = Route(route.classification, route.specialist, fallback)
                attempt = _StreamAttempt()
                async for item in attempt.run(self, route, fallback, text, ctx):
                    yield item
                if attempt.failed:
                    yield Failed()
                    return

        reply = attempt.reply()
        if not attempt.sent and reply:  # still held back: the marker, or a very short reply
            yield Token(reply)
        run = await attempt.stored_run()
        tools = tuple(t.tool_name for t in (run.tools if run else None) or [] if t.tool_name)
        metrics = run.metrics if run is not None and run.metrics is not None else attempt.metrics
        yield Done(_with_announcement(announcement, reply), route, tools, metrics)


class _StreamAttempt:
    """One streamed run of the specialist, with the marker held back."""

    def __init__(self) -> None:
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
        self, router: Router, route: Route, choice: ModelChoice, text: str, ctx: RequestContext
    ) -> AsyncIterator[Token]:
        self.agent = agent = router._agent(route, choice, ctx)
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
        """The finished run as stored: stream events miss its tool calls."""
        if self._stored is None and self.agent is not None and self.run_id is not None:
            run = await self.agent.aget_run_output(self.run_id, session_id=self.session_id)
            self._stored = run if isinstance(run, RunOutput) else None
        return self._stored

    async def needs_fallback(self) -> bool:
        """`_needs_fallback` on the stored run (stream events miss invalid tool calls)."""
        run = await self.stored_run()
        if run is None:  # can't check its tools: only a bare marker counts
            return _is_marker(self.held)
        return _needs_fallback(run)

    def reply(self) -> str:
        reply = self.completed if self.completed is not None else "".join(self.parts)
        return NOT_SURE_REPLY if _is_marker(reply) else reply
