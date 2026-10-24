"""FastAPI entry point: `uv run uvicorn alfred_server.main:app`."""

from fastapi import FastAPI

from alfred_server import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Alfred", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
