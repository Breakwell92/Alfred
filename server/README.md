# Alfred server

Python 3.12 package `alfred_server` (FastAPI + Agno), managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # install deps (incl. dev group)
uv run python -m alfred_server            # serve on ALFRED_HOST:ALFRED_PORT (default 127.0.0.1:8000), GET /health
uv run pytest                             # tests
uv run ruff check && uv run ruff format --check
uv run mypy alfred_server tests
```

Configuration comes from `ALFRED_*` environment variables and `server/.env` (start from `cp ../infra/.env.example .env`). See `alfred_server/config.py`.
