"""Agno's Postgres storage: agent sessions (conversation history), memory and traces.

Agno creates its tables on first use, in the `agno` schema of the Alfred database
(`ALFRED_DATABASE_URL`), so they never mix with Alfred's own tables.
"""

from functools import lru_cache

from agno.db.postgres import PostgresDb

from alfred_server.config import Settings, get_settings

AGNO_SCHEMA = "agno"


def get_agent_db(settings: Settings | None = None) -> PostgresDb:
    """The shared Agno database. Building it doesn't connect; the first run does."""
    settings = settings or get_settings()
    return _agent_db(settings.database_url.get_secret_value())


@lru_cache
def _agent_db(database_url: str) -> PostgresDb:
    # One instance (and connection pool) per URL for the life of the process.
    return PostgresDb(db_url=database_url, db_schema=AGNO_SCHEMA, id="alfred")
