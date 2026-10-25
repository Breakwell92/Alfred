"""`GET /ws/device`: the device WebSocket.

Not on `main.py`'s protected router, because FastAPI's bearer-token dependency doesn't run on
WebSockets. It's not open either: `DeviceConnection` checks the same `ALFRED_API_TOKEN` (header
or `hello`) before reading anything else, and refuses everything when it's unset.
"""

from __future__ import annotations

from typing import Annotated

from agno.db.base import AsyncBaseDb
from fastapi import APIRouter, Depends, WebSocket

from alfred_server.agents.router import ModelFactory, PrivacyGate, ToolLoader
from alfred_server.chat import (
    ChatBudget,
    agent_db_dependency,
    budget_dependency,
    confirmation_service_dependency,
    model_factory_dependency,
    privacy_dependency,
    routine_service_dependency,
    tool_loader_dependency,
)
from alfred_server.config import Settings, get_settings
from alfred_server.confirmations.service import ConfirmationService
from alfred_server.routines.service import RoutineService
from alfred_server.voice.gateway.connection import DeviceConnection
from alfred_server.voice.gateway.hub import DeviceHub
from alfred_server.voice.gateway.pipeline import VoiceServices
from alfred_server.voice.stt import SpeechToText
from alfred_server.voice.tts import LocalTTS, get_tts

router = APIRouter()


def voice_stt_dependency(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpeechToText | None:
    """The shared STT, or None when `ALFRED_STT_ENABLED` is off. Overridden in tests."""
    if not settings.stt_enabled:
        return None
    # Imported lazily so a server without STT never imports faster-whisper.
    from alfred_server.voice.stt import get_stt

    return get_stt()


def voice_tts_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> LocalTTS | None:
    """The shared TTS, or None when `ALFRED_TTS_ENABLED` is off. Overridden in tests."""
    return get_tts() if settings.tts_enabled else None


def get_device_hub(websocket: WebSocket) -> DeviceHub:
    hub: DeviceHub = websocket.app.state.device_hub
    return hub


@router.websocket("/ws/device")
async def device_socket(
    websocket: WebSocket,
    settings: Annotated[Settings, Depends(get_settings)],
    hub: Annotated[DeviceHub, Depends(get_device_hub)],
    db: Annotated[AsyncBaseDb, Depends(agent_db_dependency)],
    model_factory: Annotated[ModelFactory, Depends(model_factory_dependency)],
    budget: Annotated[ChatBudget, Depends(budget_dependency)],
    confirmations: Annotated[ConfirmationService, Depends(confirmation_service_dependency)],
    tools: Annotated[ToolLoader, Depends(tool_loader_dependency)],
    routines: Annotated[RoutineService, Depends(routine_service_dependency)],
    privacy: Annotated[PrivacyGate, Depends(privacy_dependency)],
    stt: Annotated[SpeechToText | None, Depends(voice_stt_dependency)],
    tts: Annotated[LocalTTS | None, Depends(voice_tts_dependency)],
) -> None:
    services = VoiceServices(
        settings=settings,
        db=db,
        model_factory=model_factory,
        budget=budget,
        confirmations=confirmations,
        tools=tools,
        stt=stt,
        tts=tts,
        # Personas (STU-66) will pick the voice per turn.
        voice=tts.default_voice if tts is not None else None,
        routines=routines,
        privacy=privacy,
    )
    await DeviceConnection(websocket, services, hub).serve()
