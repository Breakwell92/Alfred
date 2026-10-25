# Alfred

Personal, wake-word-activated Android voice assistant. A thin Android client (wake word, audio, phone actions) talks to a home server (RTX 3090) running speech-to-text, text-to-speech, local LLMs via LiteLLM, an Agno agent layer, connectors (Google, Home Assistant, Spotify, Plex, Slack, GitHub, Samsung TVs) and a scheduler. Cloud LLMs are used only when enabled, under budget and needed. Personal use only.

- Repo: https://github.com/Breakwell92/Alfred (default branch `main`)
- Tickets: Linear, team **Studio**, project **Alfred** (issue keys `STU-…`)

> STU-47 adds the repo layout, tooling and test/lint commands below the "Project layout" heading. Extend this file; don't replace the workflow rules.

## Project layout

Keep this section current whenever a ticket changes layout, commands or conventions.

| Path | What |
| --- | --- |
| `server/` | Python 3.12 package `alfred_server` (FastAPI + Agno), managed with `uv`. Tests in `server/tests/`. |
| `server/alfred_server/main.py` | FastAPI app (`app`), built by `create_app()`. `GET /health` → `{"status":"ok"}` (open); `GET /me` → `{"ok":true}` on the token-protected router. |
| `server/alfred_server/auth.py` | `require_api_token`: bearer-token check against `ALFRED_API_TOKEN` (401 wrong/missing, 503 if unset). |
| `server/alfred_server/config.py` | `Settings` (pydantic-settings) and `get_settings()`. |
| `server/alfred_server/__main__.py` | `python -m alfred_server`: runs uvicorn with host/port/log level from `Settings`. |
| `server/alfred_server/voice/stt.py` | Speech-to-text: `SpeechToText` protocol, `Transcript`, `FasterWhisperSTT`, shared `get_stt()`. The app's lifespan loads it when `ALFRED_STT_ENABLED=true`. Model choice and benchmarks in `docs/decisions/0002-stt.md`. |
| `server/scripts/bench_stt.py` | STT benchmark (latency, real-time factor, VRAM) over the WAV fixtures. |
| `server/alfred_server/voice/tts.py` | Text-to-speech: `TextToSpeech` protocol, `VoiceConfig`, `SentenceSplitter`, `LocalTTS` (Kokoro on CUDA, Piper CPU fallback), shared `get_tts()`. Output is 24 kHz mono PCM16. `GET /voices` (protected) lists voices. Loaded at startup when `ALFRED_TTS_ENABLED=true`. Engines and benchmarks in `docs/decisions/0006-tts.md`. |
| `server/scripts/download_tts_models.py`, `tts_demo.py`, `bench_tts.py` | Download Kokoro/Piper model files to `ALFRED_TTS_MODEL_DIR` (default `~/.cache/alfred/tts`); write a sample WAV per installed voice; benchmark time to first audio, CPU and VRAM. |
| `server/tests/fixtures/audio/` | 16 kHz mono 16-bit WAV clips + `transcripts.json` (synthetic voice). |
| `server/alfred_server/models/gateway.py` | `gateway_model(alias, metadata=CallMetadata(...))`: Agno `OpenAILike` model calling the LiteLLM proxy. The only way server code gets an LLM; aliases are `ModelAlias` (`local-router`, `local-main`, `cloud-claude`, `cloud-gpt`, `cloud-gemini`). See `docs/decisions/0005-model-gateway.md`. |
| `server/alfred_server/budget/` | Budget service: `get_budget_service()` → `BudgetService` with `decide(alias, prompt_tokens, expected_output_tokens, reason)` (or `estimate_cost` + `check`) → `BudgetDecision`, `get_month_to_date_spend()`, `preferred_aliases(task_type)`, `summary()`. Settings (cap, tiers, providers, preferences) persisted in Postgres (`budget_settings`, `budget_months`); spend read from LiteLLM's `LiteLLM_SpendLogs`; prices from the proxy's `/model/info`. Protected routes `GET/PUT /settings/budget`, `GET /spend/summary`. Fails closed. See `docs/decisions/0008-budget-service.md`. |
| `server/alfred_server/agents/` | Agno agent layer (`docs/decisions/0007-agent-runtime.md`): `base.py` `build_agent(name, ctx=, alias=, db=, instructions=, tools=)` shared factory (gateway model tagged with agent/trigger, history, no markdown, Agno telemetry off); `context.py` `RequestContext` rendered into each agent's system message; `storage.py` `get_agent_db()` (Agno `AsyncPostgresDb`, `agno` schema, 5 s connect timeout) and `check_agent_db()` (bounded `SELECT 1`; chat answers 503 when storage is down, because Agno swallows storage errors); `echo.py` temporary echo agent with `get_time` (the `general` specialist). Agents are built per request. |
| `server/alfred_server/agents/registry.py`, `router.py` | Router (`docs/decisions/0009-router.md`). `registry.py`: `Specialist(name, description, factory(ctx, model, db))`; register new specialists in `get_registry()` (unknown names → `general`). `router.py`: `Router(registry, budget, model_factory, db)` with `route(text, ctx)` (classify on `local-router`, then `select_model`), `run(route, …)` and `stream(route, …)`; cloud only via `budget.decide`, retry once on cloud when a local run fails; routing decision stored in run `metadata["routing"]`. Agents reply `LOW_CONFIDENCE_MARKER` (`base.py`) when unsure. |
| `server/alfred_server/chat.py` | `POST /chat` `{text, session_id?, device_id?, device_type?}` → `{reply, session_id, confirmation}` and `POST /chat/stream` (SSE `token`… then `done` `{reply, session_id, turn, confirmation}` or `error`), protected, both through the router. `turn` is how the turn ran: routing decision, specialist, tools called, tokens, `cost_gbp` (cloud only). `confirmation` is set when the turn paused before a gated tool; a plain yes/no as the session's next message answers it (`answer_pending`). Dependencies `agent_db_dependency` / `model_factory_dependency` / `budget_dependency` / `confirmation_service_dependency` are overridden in tests. |
| `server/alfred_server/confirmations/` | Confirmation gating (`docs/decisions/0013-confirmation-gating.md`): `models.py` tiers, channels, `can_approve`, `PendingConfirmation`; `policy.py` `ToolPolicy` (override, else declared risk, else `irreversible`) and `gate_agent` (sets Agno's `requires_confirmation`; the router applies it to every agent); `store.py` Postgres tables `confirmations`, `tool_permissions`, `notifications`; `service.py` `get_confirmation_service()`, `parse_yes_no`, `cancel_paused_run`, the prompts; `api.py` `GET /confirmations/pending`, `POST /confirmations/{id}` `{approve, channel}` (`irreversible` only with `channel: app_ui`), `GET /settings/permissions`, `PUT /settings/permissions/{connector}/{tool}` `{risk}`; `sweeper.py` background expiry. `Router.resume` continues an approved run. |
| `server/alfred_server/ui/` | Web chat page for testing the brain (`docs/decisions/0011-text-chat-ui.md`): `GET /ui/chat`, `/ui/chat.js`, `/ui/chat.css` from `ui/static/` (plain HTML/JS/CSS, no build step, strict CSP). Asks for the API token, streams `/chat/stream` with `device_type: text`, shows each turn's `turn` metadata, and has the Approve/Deny panel for STU-57's confirmations API. |
| `server/scripts/bench_router.py` | Router overhead benchmark: times the `local-router` classifier call (median / p90). |
| `server/alfred_server/connectors/` | Connector registry and token vault (`docs/connectors.md`, `docs/decisions/0010-connector-registry.md`): `models.py` `Connector`/`ToolMeta` (risk `read`/`write`/`irreversible`, privacy `cloud_ok`/`local_only`) and tool sources (Agno toolkit, MCP stdio, MCP streamable HTTP); `registry.py` (`tool_meta(name)`); `builtin/` built-in connectors (dummy `time`; dummy `test_messages` with the `write` tool `send_test_message`, which only records to `SENT_TEST_MESSAGES` and is on the general agent only with `ALFRED_TEST_TOOLS_ENABLED=true`); `vault.py` Fernet with `ALFRED_VAULT_KEY`; `store.py` Postgres tables (ciphertext only); `oauth.py` OAuth2 + PKCE; `service.py` `get_connector_service()` → `load_tools(specialist)`, status, refresh; `refresher.py` background token refresh; `api.py` `GET /connectors`, `PUT /connectors/{id}`, `DELETE /connectors/{id}/auth`, `GET .../auth/start` (protected) and the open, state-guarded `GET .../auth/callback`. |
| `server/tests/connector_helpers.py`, `tests/fixtures/mcp_server.py` | Test connectors, fake OAuth provider (`httpx.MockTransport`) and a tiny stdio MCP server. |
| `server/tests/llm_fakes.py`, `router_fakes.py` | `FakeProxy`: mock LiteLLM proxy (canned and streamed completions and tool calls, `route_completion()` for the classifier) for running Agno agents in tests. `FakeBudget`: canned budget answers for router tests. `FakeGate` + `read_only_connector(...)`: a confirmation gate for router tests whose own tools must be declared (undeclared tools are gated as `irreversible`). |
| `server/evals/` | Eval harness (`docs/decisions/0012-eval-harness.md`, `server/evals/README.md`): `cases/*.yaml` (~50 requests; `pending` = specialist/connector not built yet, skipped; `smoke` = the pre-merge set), `fixtures/tools.yaml` canned tool results, `cases.py` case schema, `mocks.py` tool hook that answers every tool call from fixtures (real tools never run), `runner.py` routes each case through the real router and scores specialist, required/forbidden tools, tier and reply, `report.py` markdown + JSON reports in `evals/reports/` (git-ignored), `run.py` CLI. |
| `server/alfred_server/db.py` | `check_database()`; `python -m alfred_server.db` checks `ALFRED_DATABASE_URL` is reachable. |
| `android/` | Android app `com.alfred.app` (Kotlin, Compose + Material 3, Koin, OkHttp, DataStore), Gradle KTS with a version catalog (`gradle/libs.versions.toml`). Unit tests in `app/src/test/`. See `android/README.md`. |
| `infra/` | `docker-compose.yml` (Postgres + pgvector, GPU Ollama, LiteLLM gateway), `litellm/` (proxy config + `render_config.py`), `.env.example`, `scripts/` (`pull-models.sh`, `check-models.sh`), `certs/` (git-ignored local CAs). See `docs/infra.md`. |
| `docs/` | `architecture.md`, ADRs in `docs/decisions/` (copy `0000-template.md`), plus protocol/connector/infra docs as they are written. |
| `.github/workflows/server.yml` | CI for `server/`: ruff lint, ruff format check, mypy (package and tests), pytest on every PR and push to `main`. Starts a pgvector Postgres service and sets `TEST_DATABASE_URL` so the `tests/test_db.py` smoke test runs. |
| `.github/workflows/android.yml` | CI for `android/`: `./gradlew lint testDebugUnitTest assembleDebug` on every PR and push to `main`; uploads the debug APK as an artifact. |

### Server commands (run in `server/`)

```bash
uv sync                                   # install deps incl. dev group
uv run pytest                             # unit tests
uv run ruff check                         # lint (add --fix to autofix)
uv run ruff format                        # format (CI runs --check)
uv run mypy alfred_server tests           # type check (lenient for now, pydantic plugin on)
uv run python -m alfred_server            # run the server using ALFRED_* settings (default 127.0.0.1:8000)
uv run pytest -m "not model"              # skip tests that download/load real speech models
LITELLM_TEST_URL=http://127.0.0.1:4000 LITELLM_TEST_KEY=<master key> uv run pytest -m integration   # /chat against the real gateway
uv sync --extra cuda                      # + CUDA/cuDNN wheels for GPU STT and TTS (the 3090 box; not in CI)
uv run python scripts/bench_stt.py --device cuda   # STT benchmark
uv run python -m alfred_server.db         # check the server can reach Postgres
uv run python scripts/download_tts_models.py       # Kokoro + Piper model files (~400 MB)
uv run python scripts/tts_demo.py                  # sample WAV per voice into tts-samples/
uv run python scripts/bench_tts.py                 # TTS time to first audio / VRAM
uv run python scripts/bench_router.py              # router classifier latency (needs the gateway)
uv run python -m evals.run --model-alias local-main --tag smoke   # smoke evals (needs the gateway)
uv run python -m evals.run --model-alias local-main --model-alias eval-candidate   # compare models
```

`tests/test_db.py` has a Postgres smoke test that only runs when `TEST_DATABASE_URL` is set (CI sets it with a Postgres service; locally point it at the compose stack).

Tests marked `integration` call the real LiteLLM gateway and local models (compose stack up, `local-router` and `local-main` pulled); they skip unless `LITELLM_TEST_URL` and `LITELLM_TEST_KEY` are set. The router's "ask Claude" test also needs `cloud-claude` on the proxy (`ANTHROPIC_API_KEY`) and costs a fraction of a penny; it uses an in-memory budget store, so Dan's budget settings are untouched. `tests/test_agents.py` also has a Postgres round trip of Agno sessions that runs when `TEST_DATABASE_URL` is set.

Tests marked `model` load a real model (`tiny.en` on CPU by default, downloaded once to the Hugging Face cache). Run them against the production model with `STT_TEST_MODEL=distil-large-v3 STT_TEST_DEVICE=cuda uv run pytest -m model`.

### Infra commands (see `docs/infra.md`)

```bash
cd infra && docker compose up -d          # Postgres + Ollama + LiteLLM (needs infra/.env)
infra/scripts/pull-models.sh              # pull OLLAMA_MODELS_ROUTER/MAIN (from repo root)
infra/scripts/check-models.sh             # prompt each model, show VRAM use
```

`uv run uvicorn alfred_server.main:app` also works (add `--reload` while developing), but it takes host, port and log level from its own flags and ignores `ALFRED_HOST`/`ALFRED_PORT`/`ALFRED_LOG_LEVEL`. On Windows add `--loop asyncio:SelectorEventLoop`: Agno's async Postgres storage (psycopg) can't run on uvicorn's default Windows loop; `python -m alfred_server` sets it for you.

The TTS tests marked `model` skip unless the model files have been downloaded.

Agno's `MCPTools` needs `fastmcp` 4.x (and `mcp` 2.x, where FastMCP's server class is `mcp.server.mcpserver.MCPServer`); both are dependencies.

`onnxruntime-gpu` provides the `onnxruntime` module (it also runs on CPU); `[tool.uv] override-dependencies` stops the CPU-only `onnxruntime` package that kokoro-onnx and piper-tts depend on from being installed over it. Keep it that way when adding ONNX-based packages.

CI runs `uv sync --locked`, so after changing dependencies run `uv lock` (or `uv add <pkg>`) and commit `server/uv.lock`.

On a machine whose HTTPS traffic is intercepted (uv fails with `invalid peer certificate: UnknownIssuer`), set `UV_SYSTEM_CERTS=1`.

### Android commands (run in `android/`)

Needs JDK 21 and the Android SDK (`ANDROID_HOME`, or `sdk.dir` in git-ignored `android/local.properties`). On Windows, Android Studio's bundled JBR works: `export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"` (system Java 25 is too new for Gradle/AGP).

```bash
./gradlew lint testDebugUnitTest assembleDebug   # what CI runs
./gradlew testDebugUnitTest                      # JVM unit tests only
./gradlew installDebug                           # install on a phone connected over adb
```

- The app's default server URL is `alfred.serverUrl` in `android/gradle.properties` (`http://glados:8000`, the server's Tailscale name); override with `-Palfred.serverUrl=...`. Users can change it in Settings.
- Gradle (Java) ignores the Windows certificate store. On a machine whose HTTPS is intercepted (e.g. AVG Web Shield; `PKIX path building failed` / `Could not initialize SSL context`), build a truststore that adds the intercepting root CA to the JBR's `lib/security/cacerts` with `keytool -importcert`, then `export JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore=<that file> -Djavax.net.ssl.trustStorePassword=changeit"`. `-Djavax.net.ssl.trustStoreType=Windows-ROOT` does not work with Gradle.

### Configuration

- All server config is read from environment variables by `alfred_server.config.Settings`, prefixed `ALFRED_` (e.g. `ALFRED_LOG_LEVEL`). The server also reads `server/.env` (its working directory with the commands above): for local runs, `cp infra/.env.example server/.env`.
- Add new settings as typed fields on `Settings`; get them via `get_settings()` (FastAPI: `Depends(get_settings)`), never `os.environ` directly.
- `get_settings()` is `lru_cache`d. In tests, build `Settings(...)` directly or call `get_settings.cache_clear()`; the autouse fixture in `server/tests/conftest.py` clears every `ALFRED_*` var and the cache around each test, and stops `Settings` reading `.env`, so a real `server/.env` never leaks into tests. Tests that need a value set an env var or pass it in; `Settings(_env_file=path)` still reads a given file.
- `ALFRED_HOST` defaults to `127.0.0.1`. Listening on all interfaces (`0.0.0.0`) must be set explicitly (e.g. in compose).
- Document every new variable in `infra/.env.example`, with no real values. The same file is copied to `infra/.env` (compose) and `server/.env` (server).
- Before any cloud LLM call, ask the budget service (`get_budget_service().decide(...)`) and stay local unless it allows it; say `decision.message` to the user when `decision.announce` is true.
- LLM calls go only through the LiteLLM gateway: `alfred_server.models.gateway.gateway_model("<alias>")`. Never import a provider SDK or name a provider model in server code; cloud API keys live only in `infra/.env` (read by LiteLLM).
- Secrets in `Settings` are `SecretStr` (e.g. `database_url`); read them with `.get_secret_value()` only where they're used.
- `ALFRED_API_TOKEN` (`SecretStr`) is the shared bearer token the Android app (and the web chat page) sends. Every route except `/health`, the OAuth callback `GET /connectors/{id}/auth/callback` and the web chat page's static files (`/ui/chat`, `/ui/chat.js`, `/ui/chat.css`) must require it: add routes to the `protected` router in `main.py` (or `Depends(require_api_token)`). The callback is open because the provider redirects the browser to it; it accepts only a one-time `state` from the protected `/auth/start` (ADR 0010). The chat page's files are open because a browser can't send the token when it opens a page; they hold no data or secrets, and the page sends the token on every API call (ADR 0011). Don't add other open routes without asking Dan. Unset → protected routes answer 503 (fail closed). FastAPI's `/docs`, `/redoc` and `/openapi.json` are disabled.
- Tools that change things are gated (ADR 0013): give every `write`/`irreversible` tool an honest `risk` and a `confirm_summary`, and build agents only through the router so `gate_agent` applies. Never auto-approve a gated tool, and never let anything but `channel: app_ui` approve `irreversible` ones.
- Service credentials (API keys, OAuth client secrets and tokens) live only in the connector vault (`alfred_server.connectors`), encrypted with `ALFRED_VAULT_KEY`, never in `Settings` or the phone. Every tool a connector exposes must be declared with a `ToolMeta` (risk and privacy); undeclared tools are never loaded.

### Conventions

- Python: ruff (line length 100, rules E/F/W/I/B/UP/SIM), type hints on public functions, pytest tests next to every feature.
- Kotlin: official code style, Android lint must pass (CI), JVM unit tests (JUnit 4, coroutines-test, MockWebServer) for ViewModels and data classes. Design decisions in `docs/decisions/0004-android-client-stack.md`.
- `.editorconfig` sets formatting basics; LF line endings (`.gitattributes`).
- Licence: private, all rights reserved (`LICENSE`).

## Working on tickets

### 0. Where every ticket and PR stands

Agents act through Dan's Linear and GitHub accounts, so "assigned" means assigned to Dan's account (`me`), and it is how a ticket is claimed. **Unassigned + Ready = free to pick up.** (Dan: leave Ready tickets unassigned unless you want agents to skip them.)

| Stage | Linear status | Linear assignee | PR label | Who moves it on |
| --- | --- | --- | --- | --- |
| Available | **Ready** | unassigned | — | `/ticket` agent claims it |
| Being built | **In Development** | me | — | implementing agent |
| Waiting for review | **Code Review** | me | `ready-for-review` | `/review` agent claims it |
| Being reviewed | **Code Review** | me | `in-review` | `/review` agent |
| Feedback being fixed | **In Development** | me | `changes-requested` | implementing agent (auto-fix) |
| Merged, needs Dan to verify on hardware | **QA** | me | merged | Dan moves to Done after checking |
| Merged and complete | **Done** | me | merged | — |
| Review hit the 3-round limit | **Blocked** | me | `needs-dan` | Dan (removing `needs-dan` gives 3 more rounds) |
| Stuck | **Blocked** | me | — | Dan, then back to the previous stage |

- Move the ticket the moment its stage changes, and comment on the Linear ticket at each transition (what happened, PR link).
- Swap PR labels so a PR has exactly one of `ready-for-review`, `in-review`, `changes-requested`, `needs-dan` while open.
- A blocker counts as satisfied when it is **Done** or **QA** (the code is merged; only Dan's hardware check remains).
- GitHub GraphQL isn't available in cloud sessions: use the REST API (`gh api repos/Breakwell92/Alfred/...`) for labels, reviews and merges. Resolving review threads needs GraphQL, so it's done by `/review`, which runs locally.

### 1. One ticket per session

- Start every ticket in a fresh session (or after `/clear`). Use `/ticket STU-xx` for a specific ticket, or plain `/ticket` to pick up the next free ticket: **Ready, unassigned**, blockers satisfied, highest priority, earliest milestone, runnable in this environment, not overlapping core modules with work already In Development.
- Claim the ticket immediately: assign it to `me` and move it to **In Development** before doing anything else. If it turns out to be assigned already when you go to claim it, another session got there first: pick again.
- **A session is linked to one ticket only.** The `/ticket` session is titled `STU-xx`, the review session `Review STU-xx`; each is bound to (and monitors) only that ticket's PR and works only in that ticket's branch and worktree. If a session finds a link to another ticket (another PR bound or monitored, another ticket in its title, instructions or todos), it removes it before carrying on. Only `/pm` deals with several tickets.
- Work only on the ticket you were given. If you find other work that's needed, create a new Linear ticket (team Studio, project Alfred, correct milestone, blocked-by relations set) instead of expanding scope.
- If the ticket turns out much bigger than it looks, stop after planning, propose sub-issues in Linear, and ask Dan before continuing.

### 2. Shared memory lives in the repo

Sessions don't remember each other. Anything a future session needs goes in a file, not in chat:

| What | Where |
| --- | --- |
| Conventions, layout, commands | `CLAUDE.md` (this file) |
| Design decisions and why (model choice, benchmarks, trade-offs) | `docs/decisions/NNNN-title.md` (ADR: context, decision, consequences) |
| Device ↔ server WebSocket protocol | `docs/protocol.md` |
| How to set up and add connectors | `docs/connectors.md`, `docs/connectors/<name>.md` |
| Infra, ports, env vars, GPU notes | `docs/infra.md`, `infra/.env.example` |
| Architecture overview | `docs/architecture.md` |

Before finishing a ticket, update whichever of these your change affected. Never leave a decision only in a PR description or Linear comment.

### 3. Ticket workflow

1. Read the ticket and every ticket listed as blocking it (they define the interfaces you build on). Check blockers are **Done** or **QA**; if not, stop and say so.
2. Claim it: assign to `me`, move to **In Development** (see section 1).
3. Create the branch Linear generates for the ticket (`gitBranchName`, e.g. `dpbreakwell/stu-48-…`) from up-to-date `main`.
4. Plan first for anything non-trivial (plan mode). Keep to the ticket's scope and "Out of scope" section.
5. Implement with tests. Follow existing patterns before inventing new ones.
6. Verify: lint, unit tests, and any evals the ticket names. Fix failures; don't skip or delete tests to get green.
7. Update the shared-memory docs above if affected.
8. Commit using conventional commits (`feat(router): …`, `fix(tts): …`) referencing the ticket key. Push and open a PR titled `STU-xx: <ticket title>` with: summary, how it was tested, acceptance criteria checklist (ticked or marked "needs Dan"), follow-ups.
9. Turn on auto-fix for the PR, always and without asking, **and confirm it's on before any review starts** (it's a per-PR, per-session setting, so this is done every time, including when resuming an existing PR). In the desktop app, bind the PR if needed (`ccd_pr` `get_status` / `bind_pr`), call `ccd_pr` `set_monitor` with `auto_fix: true` and `address_comments: true`, then call `get_status` again and check it shows auto-fix on for this PR (up to 3 attempts). In a cloud session, subscribe to the PR's CI checks and review comments. If auto-fix can't be confirmed, don't mark the PR ready or start a review: move the ticket to **Blocked** and tell Dan.
10. Mark the PR as ready for review: add the `ready-for-review` label. Comment on the Linear ticket with the PR link, what changed, and anything left over. Move it to **Code Review**.
11. Start the review session yourself (`Review STU-xx`, see section 5). Never leave it to the scheduled `/review` task.

**Responding to review feedback** (usually via auto-fix): when a review leaves inline comments, the PR is labelled `changes-requested` and the ticket is back in **In Development**. Fix each clear comment, push, and reply on its thread saying what changed. Don't resolve threads yourself; the reviewer resolves them once it has checked the fix. When every comment is addressed, check auto-fix is still on, swap `changes-requested` for `ready-for-review`, move the ticket to **Code Review**, and **message the `Review STU-xx` session asking it to re-review and merge if happy** (start a fresh review session if it's gone; if neither works, tell Dan plainly). If a comment is ambiguous or architectural, ask Dan and leave the labels as they are. If the PR is labelled `needs-dan`, change nothing until Dan says.

**Acceptance criteria that need hardware or a human** (real phone, GPU benchmarks, multi-hour soak tests, OAuth sign-in, listening tests): don't claim them. Mark them "Needs Dan to verify" in the PR and the Linear comment, with exact steps to verify.

**If blocked** (missing credentials, a decision only Dan can make, a blocker not done): move the ticket to **Blocked**, comment with what's needed, and stop.

### 4. Running tickets in parallel

- Only work on tickets whose blockers are all **Done** or **QA**. Use `/next` to list them, or start several sessions with plain `/ticket`: each claims a different unassigned ticket. Several `/review` sessions can run at once too: each claims a different PR via the `in-review` label.
- `/pm` is the project manager: it gives a project update (milestones, recent progress, what's outstanding, what needs Dan), then starts a fresh local `/ticket STU-xx` session for each free ticket, keeping at most **2** tickets In Development at once, and starts more as those finish. It never changes tickets or PRs itself. `/pm status` gives the update without starting anything.
- Each parallel ticket gets its own git worktree and its own session, e.g. `git worktree add ../alfred-stu-49 -b <branch> main`. Never share a working directory between sessions.
- Don't run two tickets at once that change the same core modules (router, connector registry, protocol, budget, shared `RequestContext`). If two Ready tickets overlap, do them one after the other.
- Rebase on `main` before opening the PR; resolve conflicts in your own branch.
- Hardware placement: server, model, GPU and Docker tickets run on the home server; Android tickets run where the Android SDK and phone are available.

### 5. Review in a separate session

- Never review or merge your own work in the session that wrote it.
- Reviews run in a fresh session with `/review`, which picks up the oldest open PR labelled `ready-for-review` (or `/review STU-xx` for a specific one) and claims it by swapping the label to `in-review`.
- **The implementing session always triggers the review itself.** On hand-off, `/ticket` starts a fresh local review session titled `Review STU-xx` (`/review STU-xx`, desktop app only). For each re-review it messages that same session (or starts a fresh one if it's gone). If it can't do either, it tells Dan plainly. A scheduled local `/review` task may also run as a safety net, but no session relies on it.
- **Review cycle:** `/ticket` and `/review` go back and forth until the PR is merged, with the review session reporting each outcome back to the `STU-xx` session. At most **3 review rounds**: a round is one `/review` pass, counted from the reviewer's reviews on the PR. If round 3 still has feedback, the reviewer posts it, labels the PR `needs-dan`, moves the ticket to **Blocked** and explains what Dan needs to decide. Removing `needs-dan` gives the PR a fresh 3 rounds.
- **Earlier comments are checked every round.** Before the fresh review, the reviewer goes through every unresolved thread and finding comment on the PR, checks the code, and resolves the ones that are fixed (replying "Confirmed fixed in <sha>"; conversation comments are minimized as resolved). Ones not fixed stay open with a reply saying what's missing. A PR is only merged with no unresolved threads.
- The reviewer checks the PR against the ticket's acceptance criteria and this file. It never rewrites the code itself.
- **Reviews act as a separate GitHub account** (the reviewer), so review comments aren't authored by the account that opened the PR. `/review` routes every GitHub call through `.claude/scripts/gh-as-reviewer.sh`, which loads the reviewer's token from `~/.config/alfred/reviewer-token` (on Windows, `%USERPROFILE%\.config\alfred\reviewer-token`). Reviews therefore run on a machine that has that file; without it (e.g. cloud sessions) `/review` stops. It also stops if the reviewer account is the PR's author.
- **No feedback** (nothing at all to comment on, every thread resolved, CI green, branch mergeable): the reviewer squash-merges the PR, deletes the branch, and moves the ticket to **QA** if it has "Needs Dan to verify" items, otherwise **Done**.
- **Any feedback**: the reviewer posts every finding as its own comment on the PR: inline on a file and line where possible (one review, event `COMMENT`, since the same account can't "request changes" on its own PR), otherwise as a PR conversation comment, never only in the review summary. In rounds 1 and 2 it then swaps the label to `changes-requested`, moves the ticket to **In Development** and messages the `STU-xx` session; in round 3 it hands the PR to Dan instead (see the review cycle above). The comments also trigger auto-fix on the implementing session. If auto-fix isn't on, `/ticket STU-xx` in a new session resumes from the PR and its comments.
- Merge conflicts don't trigger auto-fix on their own, so a reviewer who finds the branch out of date leaves a comment asking for a rebase, which does.
- Auto-fix acts on clear fixes only; ambiguous or architectural comments come back to Dan.

## Rules that always apply

- Never commit secrets. API keys and tokens go in `.env` (git-ignored); document new variables in `infra/.env.example`.
- Never paste real credentials into code, tests, fixtures, docs or tickets.
- Run the smoke evals (`cd server && uv run python -m evals.run --model-alias local-main --tag smoke`, see `server/evals/README.md`) before merging changes to prompts, routing or model configuration, and say the result in the PR. CI can't run them (they need the gateway and GPU).
- Safety behaviour (confirmation tiers, privacy flags, kill switch) must not be weakened to make a feature work; raise it with Dan instead.
- Prefer small, reviewable PRs. One ticket, one PR.
