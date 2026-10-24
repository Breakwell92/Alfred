"""Text chat with the agent layer: `POST /chat` and its streaming twin `POST /chat/stream`.

Both send the request through the router (docs/decisions/0009-router.md), which picks a
specialist and a model, with a `RequestContext` for the request. Turns sharing a `session_id`
share history, stored by Agno in Postgres. The stream is Server-Sent Events: `token` events
with each text delta, then one `done` event with the whole reply (or an `error` event).
See docs/decisions/0007-agent-runtime.md.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

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


def budget_dependency() -> BudgetGate:
    """The budget service the router asks before any cloud call. Overridden in tests."""
    return get_budget_service()


def router_dependency(
    db: Annotated[AsyncBaseDb, Depends(ready_agent_db)],
    model_factory: Annotated[ModelFactory, Depends(model_factory_dependency)],
    budget: Annotated[BudgetGate, Depends(budget_dependency)],
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
) -> StreamingResponse:
    route, ctx = await _route(body, settings, agent_router)
    return StreamingResponse(
        _sse_events(agent_router, route, body.text, ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_events(
    agent_router: Router, route: Route, text: str, ctx: RequestContext
) -> AsyncIterator[str]:
    session_id = ctx.session_id
    failed = _sse("error", {"message": "The agent run failed", "session_id": session_id})
    try:
        async for item in agent_router.stream(route, text, ctx):
            if isinstance(item, Token):
                yield _sse("token", {"text": item.text})
            elif isinstance(item, Done):
                yield _sse("done", {"reply": item.reply, "session_id": session_id})
            elif isinstance(item, Failed):
                yield failed
    except Exception:
        logger.exception("chat stream failed (session %s)", session_id)
        yield failed


def _sse(event: str, data: dict[str, str]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
