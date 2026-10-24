"""FastAPI entry point: `uv run uvicorn alfred_server.main:app`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status

from alfred_server import __version__
from alfred_server.auth import require_api_token
from alfred_server.budget.api import router as budget_router
from alfred_server.config import Settings, get_settings

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
    yield


def tts_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> LocalTTS:
    """The shared TTS, or 503 when it's disabled. Overridden in tests."""
    if not settings.tts_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TTS is disabled")
    return get_tts()


def create_app() -> FastAPI:
    # Interactive docs and the OpenAPI schema are off so `/health` is the only
    # unauthenticated route.
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

    app.include_router(protected)
    return app


app = create_app()
