"""Agno's Postgres storage: agent sessions (conversation history), memory and traces.

Agno creates its tables on first use, in the `agno` schema of the Alfred database
(`ALFRED_DATABASE_URL`), so they never mix with Alfred's own tables.

The db is async (`AsyncPostgresDb`) so `agent.arun()` awaits session reads and writes instead
of blocking the event loop. psycopg's async mode needs a selector event loop on Windows; see
`alfred_server.__main__`.
"""

import asyncio
from functools import lru_cache

from agno.db.base import AsyncBaseDb
from agno.db.postgres import AsyncPostgresDb
from agno.db.postgres.engine import create_async_postgres_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from alfred_server.config import Settings, get_settings

AGNO_SCHEMA = "agno"
# Fail fast when Postgres is down rather than wait for the OS TCP timeout (minutes).
CONNECT_TIMEOUT_S = 5


class StorageUnavailableError(RuntimeError):
    """The agent db can't be reached, so a run would lose its history and its result."""


def get_agent_db(settings: Settings | None = None) -> AsyncPostgresDb:
    """The shared Agno database. Building it doesn't connect; the first run does."""
    settings = settings or get_settings()
    return _agent_db(settings.database_url.get_secret_value())


@lru_cache
def _agent_db(database_url: str) -> AsyncPostgresDb:
    # One instance (and connection pool) per URL for the life of the process. Agno's engine
    # defaults include pool_pre_ping, so dropped connections are replaced.
    engine = create_async_postgres_engine(
        database_url, connect_args={"connect_timeout": CONNECT_TIMEOUT_S}
    )
    return AsyncPostgresDb(db_engine=engine, db_schema=AGNO_SCHEMA, id="alfred")


async def check_agent_db(db: AsyncBaseDb, timeout_s: float = CONNECT_TIMEOUT_S) -> None:
    """Raise `StorageUnavailableError` unless `db` answers `SELECT 1` within `timeout_s`.

    Agno's async dbs log and swallow storage errors: with Postgres down a run still "succeeds",
    with no history and nothing saved, after several connect timeouts. Callers check first so
    an outage is a quick, visible error instead. A pooled connection makes this ~1 ms.
    """
    engine = getattr(db, "db_engine", None)
    if not isinstance(engine, AsyncEngine):
        return  # not a SQL db (e.g. in-memory): nothing to reach
    try:
        async with asyncio.timeout(timeout_s), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # any driver, network or timeout error means unreachable
        raise StorageUnavailableError(f"agent db unreachable: {exc}") from exc
