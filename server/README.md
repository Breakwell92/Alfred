# Alfred server

Python 3.12 package `alfred_server` (FastAPI + Agno), managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # install deps (incl. dev group)
uv run uvicorn alfred_server.main:app     # serve on http://127.0.0.1:8000 (GET /health)
uv run pytest                             # tests
uv run ruff check && uv run ruff format --check
uv run mypy alfred_server
```

Configuration comes from `ALFRED_*` environment variables (see `alfred_server/config.py` and `infra/.env.example`).
