"""Agno's Postgres storage: agent sessions (conversation history), memory and traces.

Agno creates its tables on first use, in the `agno` schema of the Alfred database
(`ALFRED_DATABASE_URL`), so they never mix with Alfred's own tables.

The db is async (`AsyncPostgresDb`) so `agent.arun()` awaits session reads and writes instead
of blocking the event loop. psycopg's async mode needs a selector event loop on Windows; see
`alfred_server.__main__`.
"""

from functools import lru_cache

from agno.db.postgres import AsyncPostgresDb
from agno.db.postgres.engine import create_async_postgres_engine

from alfred_server.config import Settings, get_settings

AGNO_SCHEMA = "agno"
# Fail fast when Postgres is down rather than wait for the OS TCP timeout (minutes).
CONNECT_TIMEOUT_S = 5


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
