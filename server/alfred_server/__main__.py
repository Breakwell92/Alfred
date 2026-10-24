"""Run the server with settings from `ALFRED_*` env vars: `uv run python -m alfred_server`."""

import sys

import uvicorn

from alfred_server.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "alfred_server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # psycopg's async mode (Agno's AsyncPostgresDb) can't run on uvicorn's Windows default,
        # the ProactorEventLoop. Elsewhere "auto" picks uvloop, which psycopg supports.
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
