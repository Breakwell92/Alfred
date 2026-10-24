"""FastAPI entry point: `uv run uvicorn alfred_server.main:app`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from alfred_server import __version__
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
    app = FastAPI(title="Alfred", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
