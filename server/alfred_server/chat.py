"""Text chat with the agent layer: `POST /chat` and its streaming twin `POST /chat/stream`.

Both send the request through the router (docs/decisions/0009-router.md), which picks a
specialist and a model, with a `RequestContext` for the request. Turns sharing a `session_id`
share history, stored by Agno in Postgres. The stream is Server-Sent Events: `token` events
with each text delta, then one `done` event with the whole reply and a `turn` summary of how
it ran (specialist, model, why, tools, cost), or an `error` event. The web chat page
(`alfred_server/ui`, docs/decisions/0011-text-chat-ui.md) shows that summary per turn.
See docs/decisions/0007-agent-runtime.md.

A turn that reaches a gated tool pauses: the reply asks for the OK and carries the pending
`confirmation`. While one is pending, a plain "yes" or "no" as the session's next message
answers it; anything else cancels a pending `write` confirmation and is routed as usual
(docs/decisions/0013-confirmation-gating.md).

A message that is a routine's trigger phrase ("movie time") runs that routine's saved steps
instead of going to the router: no model, no cost (docs/decisions/0021-routines.md).
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from agno.db.base import AsyncBaseDb
from agno.models.base import Model
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alfred_server.agents.context import ID_PATTERN, DeviceType, RequestContext
from alfred_server.agents.registry import get_registry
from alfred_server.agents.router import (
    AgentRunError,
    BudgetGate,
    Done,
    Failed,
    ModelFactory,
    Route,
    RoutedReply,
    Router,
    Token,
    ToolLoader,
)
from alfred_server.agents.storage import StorageUnavailableError, check_agent_db, get_agent_db
from alfred_server.budget.models import CostEstimate
from alfred_server.budget.service import get_budget_service
from alfred_server.config import Settings, get_settings
from alfred_server.confirmations.models import CHANNEL_FOR_DEVICE, PendingConfirmation, can_approve
from alfred_server.confirmations.service import (
    APP_ONLY_REPLY,
    ConfirmationService,
    ConfirmationsUnavailableError,
    YesNo,
    cancel_paused_run,
    denied_reply,
    get_confirmation_service,
    parse_yes_no,
)
from alfred_server.connectors.service import get_connector_service
from alfred_server.models.gateway import (
    CallMetadata,
    GatewayNotConfiguredError,
    ModelAlias,
    gateway_model,
)
from alfred_server.routines.models import RoutineResult
from alfred_server.routines.service import RoutineService, get_routine_service

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # Omit to start a new conversation; the response carries the id to send next time.
    # Ids go into the system prompt, so only a safe character set (no newlines or markup).
    session_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    device_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    device_type: DeviceType = "text"


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    # Set when the reply asks for the OK to run a gated tool (see `PendingConfirmation.public`).
    confirmation: dict[str, Any] | None = None


def agent_db_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> AsyncBaseDb:
    """Agno storage for sessions. Overridden in tests."""
    return get_agent_db(settings)


async def ready_agent_db(
    db: Annotated[AsyncBaseDb, Depends(agent_db_dependency)],
) -> AsyncBaseDb:
    """The agent db, or 503 if it can't be reached within a few seconds.

    Without this, Agno would answer without history and silently drop the turn.
    """
    try:
        await check_agent_db(db)
    except StorageUnavailableError as exc:
        logger.error("chat refused: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Conversation storage is unavailable"
        ) from exc
    return db


def model_factory_dependency(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelFactory:
    """Gateway models by alias. Overridden in tests with mocked ones."""

    def factory(alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model:
        return gateway_model(alias, metadata=metadata, settings=settings, **kwargs)

    return factory


class ChatBudget(BudgetGate, Protocol):
    """The router's budget gate, plus pricing for the cost shown with each streamed turn."""

    def estimate_cost(
        self, alias: ModelAlias, prompt_tokens: int, expected_output_tokens: int
    ) -> CostEstimate: ...


def budget_dependency() -> ChatBudget:
    """The budget service the router asks before any cloud call. Overridden in tests."""
    return get_budget_service()


def confirmation_service_dependency() -> ConfirmationService:
    """Pending confirmations and permission overrides. Overridden in tests."""
    return get_confirmation_service()


def tool_loader_dependency() -> ToolLoader:
    """Connector tools for specialists that use them (Spotify for media). Overridden in tests."""
    return get_connector_service().load_tools


def routine_service_dependency() -> RoutineService:
    """Saved routines, matched before the router. Overridden in tests."""
    return get_routine_service()


def router_dependency(
    db: Annotated[AsyncBaseDb, Depends(ready_agent_db)],
    model_factory: Annotated[ModelFactory, Depends(model_factory_dependency)],
    budget: Annotated[ChatBudget, Depends(budget_dependency)],
    confirmations: Annotated[ConfirmationService, Depends(confirmation_service_dependency)],
    tools: Annotated[ToolLoader, Depends(tool_loader_dependency)],
) -> Router:
    return Router(get_registry(), budget, model_factory, db, confirmations, tools)


@dataclass(frozen=True)
class Answered:
    """The message answered a pending confirmation instead of starting a new request."""

    reply: str
    # The resumed run, if the answer was yes and it ran.
    routed: RoutedReply | None = None
    # Still (or newly) pending: an app-only approval, or a run that paused again.
    confirmation: PendingConfirmation | None = None


async def answer_pending(
    text: str,
    ctx: RequestContext,
    confirmations: ConfirmationService,
    agent_router: Router,
    parse: Callable[[str], Awaitable[YesNo | None]] | None = None,
) -> Answered | None:
    """Answer this session's pending confirmation with `text`, or None to route it as usual.

    `text` is a yes or no if `parse_yes_no` says so, else if `parse` (e.g. the voice gateway's
    model-based check, for "yeah go on then") does. It's only called when one is pending.
    Raises `ConfirmationsUnavailableError` if the confirmations can't be read and
    `AgentRunError` if an approved run fails to continue.
    """
    db = agent_router.db
    await confirmations.expire_due(db)
    pending = await asyncio.to_thread(confirmations.pending, ctx.session_id)
    if not pending:
        return None
    answer = parse_yes_no(text)
    if answer is None and parse is not None:
        answer = await parse(text)
    if answer is None:
        # The user moved on: a later "yes" mustn't approve something they've stopped
        # thinking about. Irreversible ones wait for the app (or expiry).
        for c in pending:
            if c.tier != "irreversible" and await asyncio.to_thread(
                confirmations.resolve, c, "cancelled"
            ):
                await cancel_paused_run(db, c, "Cancelled: the user moved on.")
        return None
    c = pending[-1]  # the newest: what the last reply asked about
    if answer == "no":
        if await asyncio.to_thread(confirmations.resolve, c, "denied"):
            await cancel_paused_run(db, c, denied_reply(c))
        return Answered(denied_reply(c))
    tier = await asyncio.to_thread(confirmations.current_tier, c)
    if not can_approve(tier, CHANNEL_FOR_DEVICE[ctx.device_type]):
        return Answered(APP_ONLY_REPLY, confirmation=c)
    if not await asyncio.to_thread(confirmations.resolve, c, "approved"):
        return Answered("That's already been dealt with.")
    routed = await agent_router.resume(c, ctx)
    return Answered(routed.reply, routed, routed.confirmation)


def _context(body: ChatRequest, settings: Settings) -> RequestContext:
    return RequestContext.build(
        timezone=settings.timezone,
        device_type=body.device_type,
        session_id=body.session_id or str(uuid.uuid4()),
        device_id=body.device_id,
    )


def _unavailable(exc: ConfirmationsUnavailableError) -> HTTPException:
    logger.error("chat refused: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


async def _route(body: ChatRequest, ctx: RequestContext, router: Router) -> Route:
    try:
        return await router.route(body.text, ctx)
    except GatewayNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


async def _answer(
    body: ChatRequest,
    ctx: RequestContext,
    confirmations: ConfirmationService,
    agent_router: Router,
) -> Answered | None:
    try:
        return await answer_pending(body.text, ctx, confirmations, agent_router)
    except ConfirmationsUnavailableError as exc:
        raise _unavailable(exc) from exc
    except GatewayNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AgentRunError as exc:
        logger.error("resuming a run failed (session %s): %s", ctx.session_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed") from exc
    except Exception as exc:
        # e.g. Postgres down while reading the pending confirmations.
        logger.exception("couldn't check pending confirmations (session %s)", ctx.session_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Confirmations are unavailable"
        ) from exc


async def run_routine(
    text: str, ctx: RequestContext, routines: RoutineService
) -> RoutineResult | None:
    """Run the routine `text` triggers, or None to route it as usual.

    Raises `ConfirmationsUnavailableError` when a routine matched but the tool policy can't be
    read (no step runs unchecked).
    """
    return await routines.match_and_run(text, ctx.device_type)


async def _routine(
    body: ChatRequest, ctx: RequestContext, routines: RoutineService
) -> RoutineResult | None:
    try:
        return await run_routine(body.text, ctx, routines)
    except ConfirmationsUnavailableError as exc:
        raise _unavailable(exc) from exc


def _public(c: PendingConfirmation | None) -> dict[str, Any] | None:
    return None if c is None else c.public()


@router.post("/chat")
async def chat(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    agent_router: Annotated[Router, Depends(router_dependency)],
    confirmations: Annotated[ConfirmationService, Depends(confirmation_service_dependency)],
    routines: Annotated[RoutineService, Depends(routine_service_dependency)],
) -> ChatResponse:
    ctx = _context(body, settings)
    answered = await _answer(body, ctx, confirmations, agent_router)
    if answered is not None:
        return ChatResponse(
            reply=answered.reply,
            session_id=ctx.session_id,
            confirmation=_public(answered.confirmation),
        )
    ran = await _routine(body, ctx, routines)
    if ran is not None:
        return ChatResponse(reply=ran.reply, session_id=ctx.session_id)
    route = await _route(body, ctx, agent_router)
    # Agno reports model failures as an errored run (AgentRunError); the bare except is a
    # backstop for anything it lets escape. Storage was checked by `ready_agent_db`.
    try:
        result = await agent_router.run(route, body.text, ctx)
    except ConfirmationsUnavailableError as exc:
        raise _unavailable(exc) from exc
    except AgentRunError as exc:
        logger.error("chat run failed (session %s): %s", ctx.session_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed") from exc
    except Exception as exc:
        logger.exception("chat run failed (session %s)", ctx.session_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed") from exc
    return ChatResponse(
        reply=result.reply,
        session_id=ctx.session_id,
        confirmation=_public(result.confirmation),
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    agent_router: Annotated[Router, Depends(router_dependency)],
    budget: Annotated[ChatBudget, Depends(budget_dependency)],
    confirmations: Annotated[ConfirmationService, Depends(confirmation_service_dependency)],
    routines: Annotated[RoutineService, Depends(routine_service_dependency)],
) -> StreamingResponse:
    ctx = _context(body, settings)
    answered = await _answer(body, ctx, confirmations, agent_router)
    events: AsyncIterator[str]
    if answered is not None:
        events = _answered_events(answered, budget, ctx)
    elif (ran := await _routine(body, ctx, routines)) is not None:
        events = _routine_events(ran, ctx)
    else:
        route = await _route(body, ctx, agent_router)
        events = _sse_events(agent_router, budget, route, body.text, ctx)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _answered_events(
    answered: Answered, budget: ChatBudget, ctx: RequestContext
) -> AsyncIterator[str]:
    """An answered confirmation as a stream: the whole reply as one token, then `done`."""
    yield _sse("token", {"text": answered.reply})
    turn = None
    if answered.routed is not None:
        run = answered.routed.run
        tools = tuple(t.tool_name for t in run.tools or [] if t.tool_name)
        done = Done(answered.reply, answered.routed.route, tools, run.metrics)
        turn = await turn_info(done, budget)
    yield _sse(
        "done",
        {
            "reply": answered.reply,
            "session_id": ctx.session_id,
            "turn": turn,
            "confirmation": _public(answered.confirmation),
        },
    )


async def _routine_events(ran: RoutineResult, ctx: RequestContext) -> AsyncIterator[str]:
    """A routine run as a stream: the whole reply as one token, then `done`."""
    yield _sse("token", {"text": ran.reply})
    yield _sse(
        "done",
        {
            "reply": ran.reply,
            "session_id": ctx.session_id,
            "turn": ran.turn_info(),
            "confirmation": None,
        },
    )


async def _sse_events(
    agent_router: Router, budget: ChatBudget, route: Route, text: str, ctx: RequestContext
) -> AsyncIterator[str]:
    session_id = ctx.session_id
    failed = _sse("error", {"message": "The agent run failed", "session_id": session_id})
    try:
        async for item in agent_router.stream(route, text, ctx):
            if isinstance(item, Token):
                yield _sse("token", {"text": item.text})
            elif isinstance(item, Done):
                turn = await turn_info(item, budget)
                yield _sse(
                    "done",
                    {
                        "reply": item.reply,
                        "session_id": session_id,
                        "turn": turn,
                        "confirmation": _public(item.confirmation),
                    },
                )
            elif isinstance(item, Failed):
                yield failed
    except Exception:
        logger.exception("chat stream failed (session %s)", session_id)
        yield failed


async def turn_info(done: Done, budget: ChatBudget) -> dict[str, Any]:
    """How the turn ran, for the web chat and the voice gateway's `turn.metadata`: the routing
    decision, tool calls and cost.

    Cost is `None` on local models, and on cloud when it can't be priced. It's the proxy's
    price for the run's tokens, so close to (not exactly) what LiteLLM records as spend.
    """
    choice = done.route.choice
    metrics = done.metrics
    input_tokens = metrics.input_tokens if metrics else 0
    output_tokens = metrics.output_tokens if metrics else 0
    cost_gbp: float | None = None
    if choice.tier == "cloud":
        try:
            estimate = await asyncio.to_thread(
                budget.estimate_cost, choice.alias, input_tokens, output_tokens
            )
            cost_gbp = round(estimate.gbp, 6)
        except Exception:
            logger.warning("couldn't price the turn on %s", choice.alias, exc_info=True)
    return {
        **choice.metadata(),
        "specialist": done.route.specialist.name,
        "tools": list(done.tools),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_gbp": cost_gbp,
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
