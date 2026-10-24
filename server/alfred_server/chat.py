"""Text chat with the agent layer: `POST /chat` and its streaming twin `POST /chat/stream`.

Both send the request through the router (docs/decisions/0009-router.md), which picks a
specialist and a model, with a `RequestContext` for the request. Turns sharing a `session_id`
share history, stored by Agno in Postgres. The stream is Server-Sent Events: `token` events
with each text delta, then one `done` event with the whole reply and a `turn` summary of how
it ran (specialist, model, why, tools, cost), or an `error` event. The web chat page
(`alfred_server/ui`, docs/decisions/0011-text-chat-ui.md) shows that summary per turn.
See docs/decisions/0007-agent-runtime.md.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
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
    Router,
    Token,
)
from alfred_server.agents.storage import StorageUnavailableError, check_agent_db, get_agent_db
from alfred_server.budget.models import CostEstimate
from alfred_server.budget.service import get_budget_service
from alfred_server.config import Settings, get_settings
from alfred_server.models.gateway import (
    CallMetadata,
    GatewayNotConfiguredError,
    ModelAlias,
    gateway_model,
)

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


def router_dependency(
    db: Annotated[AsyncBaseDb, Depends(ready_agent_db)],
    model_factory: Annotated[ModelFactory, Depends(model_factory_dependency)],
    budget: Annotated[ChatBudget, Depends(budget_dependency)],
) -> Router:
    return Router(get_registry(), budget, model_factory, db)


async def _route(
    body: ChatRequest, settings: Settings, router: Router
) -> tuple[Route, RequestContext]:
    ctx = RequestContext.build(
        timezone=settings.timezone,
        device_type=body.device_type,
        session_id=body.session_id or str(uuid.uuid4()),
        device_id=body.device_id,
    )
    try:
        route = await router.route(body.text, ctx)
    except GatewayNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return route, ctx


@router.post("/chat")
async def chat(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    agent_router: Annotated[Router, Depends(router_dependency)],
) -> ChatResponse:
    route, ctx = await _route(body, settings, agent_router)
    # Agno reports model failures as an errored run (AgentRunError); the bare except is a
    # backstop for anything it lets escape. Storage was checked by `ready_agent_db`.
    try:
        result = await agent_router.run(route, body.text, ctx)
    except AgentRunError as exc:
        logger.error("chat run failed (session %s): %s", ctx.session_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed") from exc
    except Exception as exc:
        logger.exception("chat run failed (session %s)", ctx.session_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed") from exc
    return ChatResponse(reply=result.reply, session_id=ctx.session_id)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    agent_router: Annotated[Router, Depends(router_dependency)],
    budget: Annotated[ChatBudget, Depends(budget_dependency)],
) -> StreamingResponse:
    route, ctx = await _route(body, settings, agent_router)
    return StreamingResponse(
        _sse_events(agent_router, budget, route, body.text, ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
                turn = await _turn_info(item, budget)
                yield _sse("done", {"reply": item.reply, "session_id": session_id, "turn": turn})
            elif isinstance(item, Failed):
                yield failed
    except Exception:
        logger.exception("chat stream failed (session %s)", session_id)
        yield failed


async def _turn_info(done: Done, budget: ChatBudget) -> dict[str, Any]:
    """How the turn ran, for the web chat: the routing decision, tool calls and cost.

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
