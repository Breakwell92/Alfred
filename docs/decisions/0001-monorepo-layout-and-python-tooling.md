# 0001: Monorepo layout and Python tooling

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-47

## Context

Alfred has an Android client, a Python home server, infrastructure config and documentation. Many short-lived Claude Code sessions work on it in parallel, so the layout and commands need to be predictable and written down.

## Decision

- One repo with top-level `server/`, `android/`, `infra/` and `docs/`.
- Server: Python 3.12, managed with `uv` (`server/pyproject.toml`, `server/uv.lock` committed). FastAPI for HTTP/WebSocket, Agno for agents.
- Quality: `pytest`, `ruff` (lint + format), `mypy` in lenient mode (tightened per module later).
- Config: `pydantic-settings` `Settings` class in `alfred_server/config.py`, env vars prefixed `ALFRED_`, documented in `infra/.env.example`.
- CI: GitHub Actions runs lint, format check, mypy and tests for `server/` on PRs and pushes to `main`. Android CI comes with STU-51.
- Licence: private, all rights reserved.

## Consequences

- Every session runs the same commands (listed in `CLAUDE.md`).
- `uv.lock` must be updated (`uv lock`) whenever dependencies change, or CI fails on `uv sync --locked`.
