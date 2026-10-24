"""FastAPI entry point: `uv run uvicorn alfred_server.main:app`."""

from fastapi import APIRouter, Depends, FastAPI

from alfred_server import __version__
from alfred_server.auth import require_api_token


def create_app() -> FastAPI:
    # Interactive docs and the OpenAPI schema are off so `/health` is the only
    # unauthenticated route.
    app = FastAPI(
        title="Alfred", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
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
