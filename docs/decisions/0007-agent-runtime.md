# 0007: Agent runtime: Agno agents in the FastAPI app, without AgentOS

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-53

## Context

Agno is Alfred's agent layer: a router (STU-55) hands requests to specialist agents, each with only its own tools, and every model comes from the LiteLLM gateway (ADR 0005). The server needed the Agno runtime wired in with persistent conversations, shared agent defaults, per-request context (time, device, room, persona) and a first endpoint to talk to an agent.

Agno ships AgentOS, a FastAPI app that serves agents plus sessions, memory, approvals, schedules, knowledge, an MCP server and more (about a hundred routes), with its own auth, CORS for agno.com's web UI and telemetry to agno.com on by default.

## Decision

- **No AgentOS for now (Dan's call).** The server uses `agno.agent.Agent` and `agno.db.postgres.PostgresDb` directly and adds only the routes it needs, on the existing token-protected router. Nothing Alfred needs yet depends on AgentOS, and every extra route would have to be put behind `ALFRED_API_TOKEN` and tested. A later ticket can mount it (behind the same auth) if its web UI or APIs are wanted.
- **Agent factory.** `alfred_server.agents.base.build_agent(name, ctx=, alias=, db=, instructions=, tools=)` gives every agent the same defaults: the gateway model for `alias`, tagged with `CallMetadata(agent=name, trigger=ctx.device_type)`; session history from the db (`add_history_to_context`, last 10 runs); `markdown=False` and short, plain, British-English instructions because replies are spoken; the user's timezone; and Agno telemetry off (`telemetry=False`; nothing leaves the house unless a cloud model is chosen).
- **Agents are built per request.** Building an Agno agent is cheap (no I/O). Doing it per request lets the request context and the `trigger` tag vary per run without shared mutable state, and it is the shape the router's registry will use (a factory per specialist).
- **`RequestContext`** (`alfred_server/agents/context.py`): current time (in the user's timezone), timezone, device type (`phone` / `text` / `scheduled`), session id, and optional device id, location, active room, plus persona id (`default` until personas exist). The factory renders it as a `<request_context>` block in the agent's system message (Agno `additional_context`), leaving out unset fields.
- **Storage.** Agno's tables (sessions with their runs, memory, traces) live in the `agno` schema of the Alfred database (`ALFRED_DATABASE_URL`), created by Agno on first use. One `PostgresDb` per process (`get_agent_db()`).
- **Tracing.** With `ALFRED_AGENT_TRACING=true`, the lifespan calls `agno.tracing.setup_tracing` (OpenTelemetry + OpenInference), which stores spans for agent runs, model calls and tool calls in the same schema. Off by default so tests never need a database. Agno's own log lines go to stdout.
- **Chat endpoints** (`alfred_server/chat.py`), both requiring the API token:
  - `POST /chat` `{"text", "session_id"?, "device_id"?, "device_type"?}` → `{"reply", "session_id"}`. No `session_id` starts a new conversation and the response carries the id to reuse. 503 when the gateway key isn't set, 502 when the run fails.
  - `POST /chat/stream`, same body, answers `text/event-stream`: `event: token` / `data: {"text": "..."}` per text delta, then `event: done` / `data: {"reply", "session_id"}`, or `event: error` / `data: {"message", "session_id"}`.
  - They run a temporary `echo` agent on `local-main` with one tool, `get_time`. STU-55 replaces it with the router (registering it as `general` until specialists exist).

## Consequences

- Specialists are a factory function calling `build_agent` with their instructions and tools; the router chooses the alias.
- History is per `session_id`, not per user: a single-user assistant. `user_id` can be added when the app sends one.
- Mocked tests run Agno for real against a fake LiteLLM proxy (`tests/llm_fakes.py`) with `InMemoryDb`; a Postgres round trip runs in CI (`TEST_DATABASE_URL`), and `pytest -m integration` hits the real gateway (`LITELLM_TEST_URL`, `LITELLM_TEST_KEY`).
- `agno` is pinned to an exact version: it releases often and changes APIs; bump it deliberately and rerun the tests.
