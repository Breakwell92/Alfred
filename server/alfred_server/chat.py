"""Text chat with the agent layer: `POST /chat` and its streaming twin `POST /chat/stream`.

Both run the temporary echo agent (the router replaces it in STU-55) with a `RequestContext`
for the request. Turns sharing a `session_id` share history, stored by Agno in Postgres.
The stream is Server-Sent Events: `token` events with each text delta, then one `done` event
with the whole reply (or an `error` event). See docs/decisions/0007-agent-runtime.md.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.run.agent import RunCompletedEvent, RunContentEvent, RunErrorEvent
from agno.run.base import RunStatus
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alfred_server.agents.context import DeviceType, RequestContext
from alfred_server.agents.echo import build_echo_agent
from alfred_server.agents.storage import get_agent_db
from alfred_server.config import Settings, get_settings
from alfred_server.models.gateway import GatewayNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # Omit to start a new conversation; the response carries the id to send next time.
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    device_type: DeviceType = "text"


class ChatResponse(BaseModel):
    reply: str
    session_id: str


def agent_db_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> BaseDb:
    """Agno storage for sessions. Overridden in tests."""
    return get_agent_db(settings)


def chat_model_dependency() -> Model | None:
    """None = the gateway model the agent asks for. Overridden in tests with a mocked one."""
    return None


def _build_agent(
    body: ChatRequest, settings: Settings, db: BaseDb, model: Model | None
) -> tuple[Agent, str]:
    session_id = body.session_id or str(uuid.uuid4())
    ctx = RequestContext.build(
        timezone=settings.timezone,
        device_type=body.device_type,
        session_id=session_id,
        device_id=body.device_id,
    )
    try:
        agent = build_echo_agent(ctx, db=db, settings=settings, model=model)
    except GatewayNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return agent, session_id


@router.post("/chat")
async def chat(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[BaseDb, Depends(agent_db_dependency)],
    model: Annotated[Model | None, Depends(chat_model_dependency)],
) -> ChatResponse:
    agent, session_id = _build_agent(body, settings, db, model)
    run = await agent.arun(body.text, session_id=session_id)
    if run.status == RunStatus.error or not isinstance(run.content, str):
        logger.error("chat run failed (session %s): %s", session_id, run.content)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed")
    return ChatResponse(reply=run.content, session_id=session_id)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[BaseDb, Depends(agent_db_dependency)],
    model: Annotated[Model | None, Depends(chat_model_dependency)],
) -> StreamingResponse:
    agent, session_id = _build_agent(body, settings, db, model)
    return StreamingResponse(
        _sse_events(agent, body.text, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_events(agent: Agent, text: str, session_id: str) -> AsyncIterator[str]:
    parts: list[str] = []
    try:
        async for event in agent.arun(text, session_id=session_id, stream=True):
            if isinstance(event, RunContentEvent) and isinstance(event.content, str):
                if event.content:
                    parts.append(event.content)
                    yield _sse("token", {"text": event.content})
            elif isinstance(event, RunErrorEvent):
                logger.error("chat stream failed (session %s): %s", session_id, event.content)
                yield _sse("error", {"message": "The agent run failed", "session_id": session_id})
                return
            elif isinstance(event, RunCompletedEvent) and isinstance(event.content, str):
                # The completed run's content is authoritative (deltas may skip tool output).
                parts = [event.content]
    except Exception:
        logger.exception("chat stream failed (session %s)", session_id)
        yield _sse("error", {"message": "The agent run failed", "session_id": session_id})
        return
    yield _sse("done", {"reply": "".join(parts), "session_id": session_id})


def _sse(event: str, data: dict[str, str]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
