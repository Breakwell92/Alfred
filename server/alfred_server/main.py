"""FastAPI entry point: `uv run uvicorn alfred_server.main:app`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status

from alfred_server import __version__
from alfred_server.auth import require_api_token
from alfred_server.budget.api import router as budget_router
from alfred_server.chat import router as chat_router
from alfred_server.config import Settings, get_settings
from alfred_server.confirmations.api import router as confirmations_router
from alfred_server.connectors.api import callback_router as connector_callback_router
from alfred_server.connectors.api import router as connector_router
from alfred_server.connectors.refresher import TokenRefresher
from alfred_server.connectors.service import get_connector_service
from alfred_server.ui.routes import router as ui_router
from alfred_server.voice.gateway.hub import DeviceHub
from alfred_server.voice.gateway.routes import router as device_socket_router

# Cheap to import: the TTS engines themselves are imported when they load.
from alfred_server.voice.tts import LocalTTS, get_tts, voices_response


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if get_settings().stt_enabled:
        # Imported lazily so a server without STT never imports faster-whisper.
        from alfred_server.voice.stt import get_stt

        # Load the shared model once, before the first request, and warm it up.
        await asyncio.to_thread(lambda: get_stt().warmup())
    if get_settings().tts_enabled:
        # Loads Kokoro (warmed up) and the Piper fallback voice.
        await asyncio.to_thread(get_tts)
    if get_settings().agent_tracing:
        from agno.tracing import setup_tracing

        from alfred_server.agents.storage import get_agent_db

        # Process-wide OpenTelemetry provider exporting agent spans to the `agno` schema.
        setup_tracing(db=get_agent_db(), batch_processing=True)
    refresher: TokenRefresher | None = None
    vault_key = get_settings().vault_key
    if vault_key is not None and vault_key.get_secret_value():
        # Renews OAuth tokens before they expire. Without a vault key there are none.
        refresher = TokenRefresher(
            get_connector_service(), get_settings().connector_refresh_interval_s
        )
        refresher.start()
    # Cancels paused runs whose confirmation nobody answered in time.
    from alfred_server.agents.storage import get_agent_db
    from alfred_server.confirmations.service import get_confirmation_service
    from alfred_server.confirmations.sweeper import ConfirmationSweeper

    sweeper = ConfirmationSweeper(
        get_confirmation_service(), get_agent_db(), get_settings().confirmation_sweep_interval_s
    )
    sweeper.start()
    yield
    await sweeper.stop()
    if refresher is not None:
        await refresher.stop()
    if get_connector_service.cache_info().currsize:
        # Stop stdio MCP servers and close remote MCP sessions (also for connectors that need
        # no vault key). Skipped if the service was never built, so shutdown makes no engine.
        await get_connector_service().aclose()
    if get_settings().agent_tracing:
        from opentelemetry import trace

        # Export spans still queued in the batch processor before the process exits.
        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()


def tts_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> LocalTTS:
    """The shared TTS, or 503 when it's disabled. Overridden in tests."""
    if not settings.tts_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TTS is disabled")
    return get_tts()


def create_app() -> FastAPI:
    # Interactive docs and the OpenAPI schema are off. The only unauthenticated routes are
    # `/health`, the chat page's static files and the OAuth callback (see below); the device
    # WebSocket authenticates itself.
    app = FastAPI(
        title="Alfred",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # The web chat page's static files are open too: a browser can't send the bearer token
    # when it opens a page. They hold no data; every API call the page makes is protected.
    app.include_router(ui_router)

    # Every other route goes on this router so it requires the API token.
    protected = APIRouter(dependencies=[Depends(require_api_token)])

    @protected.get("/me")
    def me() -> dict[str, bool]:
        return {"ok": True}

    @protected.get("/voices")
    def voices(tts: Annotated[LocalTTS, Depends(tts_dependency)]) -> dict[str, Any]:
        """Installed TTS voices per engine, the default voice and the output sample rate."""
        return voices_response(tts)

    # GET/PUT /settings/budget, GET /spend/summary.
    protected.include_router(budget_router)

    protected.include_router(chat_router)

    # GET /confirmations/pending, POST /confirmations/{id}, GET /settings/permissions,
    # PUT /settings/permissions/{connector}/{tool}.
    protected.include_router(confirmations_router)

    # GET /connectors, PUT /connectors/{id}, DELETE /connectors/{id}/auth, GET .../auth/start.
    protected.include_router(connector_router)
    app.include_router(protected)

    # The only other open route: the OAuth provider redirects the browser here, so it can't
    # send the API token. It accepts only a one-time state from the protected /auth/start.
    app.include_router(connector_callback_router)

    # The device WebSocket checks the API token itself (header or `hello`): FastAPI's bearer
    # dependency doesn't run on WebSockets. See alfred_server/voice/gateway/routes.py.
    app.state.device_hub = DeviceHub()
    app.include_router(device_socket_router)
    return app


app = create_app()
