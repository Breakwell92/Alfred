"""FastAPI entry point: `uv run uvicorn alfred_server.main:app`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from alfred_server import __version__
from alfred_server.auth import require_api_token
from alfred_server.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if get_settings().stt_enabled:
        # Imported lazily so a server without STT never imports faster-whisper.
        from alfred_server.voice.stt import get_stt

        # Load the shared model once, before the first request, and warm it up.
        await asyncio.to_thread(lambda: get_stt().warmup())
    yield


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

    app.include_router(protected)
    return app


app = create_app()
