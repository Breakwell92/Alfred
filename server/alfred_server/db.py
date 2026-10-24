"""Postgres connectivity.

`python -m alfred_server.db` checks that the server can reach the database configured by
`ALFRED_DATABASE_URL` and prints the Postgres version.
"""

import sys

from sqlalchemy import create_engine, text

from alfred_server.config import get_settings


def check_database(database_url: str, timeout_seconds: int = 5) -> str:
    """Connect to `database_url`, run a trivial query and return the server version.

    Raises `sqlalchemy.exc.OperationalError` if the database can't be reached.
    """
    engine = create_engine(database_url, connect_args={"connect_timeout": timeout_seconds})
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
    finally:
        engine.dispose()
    return str(version)


def main() -> int:
    settings = get_settings()
    try:
        version = check_database(settings.database_url.get_secret_value())
    except Exception as exc:  # report any failure as a clean CLI error
        print(f"database check failed: {exc}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
