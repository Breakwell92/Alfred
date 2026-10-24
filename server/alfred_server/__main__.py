"""Run the server with settings from `ALFRED_*` env vars: `uv run python -m alfred_server`."""

import uvicorn

from alfred_server.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "alfred_server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
