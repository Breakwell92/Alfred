"""Run the server with settings from `ALFRED_*` env vars: `uv run python -m alfred_server`."""

import copy
import sys
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from alfred_server.config import get_settings


def log_config(level: str) -> dict[str, Any]:
    """uvicorn's logging config, plus the server's own `alfred_server.*` loggers at `level`.

    Without this only uvicorn's loggers have a handler, and the app's INFO lines (routing
    decisions, voice turn timings) go nowhere.
    """
    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["alfred"] = {
        "()": "uvicorn.logging.DefaultFormatter",
        "fmt": "%(levelprefix)s %(name)s: %(message)s",
        "use_colors": None,
    }
    config["handlers"]["alfred"] = {
        "formatter": "alfred",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
    }
    config["loggers"]["alfred_server"] = {
        "handlers": ["alfred"],
        "level": level,
        "propagate": False,
    }
    return config


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "alfred_server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=log_config(settings.log_level),
        # psycopg's async mode (Agno's AsyncPostgresDb) can't run on uvicorn's Windows default,
        # the ProactorEventLoop. Elsewhere "auto" picks uvloop, which psycopg supports.
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
