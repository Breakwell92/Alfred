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
| `android/` | Android app `com.alfred.app` (Kotlin, Compose + Material 3, Koin, OkHttp, DataStore), Gradle KTS with a version catalog (`gradle/libs.versions.toml`). Unit tests in `app/src/test/`. See `android/README.md`. |
| `infra/` | `docker-compose.yml` (services added from STU-48) and `.env.example`. |
| `docs/` | `architecture.md`, ADRs in `docs/decisions/` (copy `0000-template.md`), plus protocol/connector/infra docs as they are written. |
| `.github/workflows/server.yml` | CI for `server/`: ruff lint, ruff format check, mypy (package and tests), pytest on every PR and push to `main`. |
| `.github/workflows/android.yml` | CI for `android/`: `./gradlew lint testDebugUnitTest assembleDebug` on every PR and push to `main`; uploads the debug APK as an artifact. |

### Server commands (run in `server/`)

```bash
uv sync                                   # install deps incl. dev group
uv run pytest                             # unit tests
uv run ruff check                         # lint (add --fix to autofix)
uv run ruff format                        # format (CI runs --check)
uv run mypy alfred_server tests           # type check (lenient for now, pydantic plugin on)
uv run python -m alfred_server            # run the server using ALFRED_* settings (default 127.0.0.1:8000)
```

`uv run uvicorn alfred_server.main:app` also works (add `--reload` while developing), but it takes host, port and log level from its own flags and ignores `ALFRED_HOST`/`ALFRED_PORT`/`ALFRED_LOG_LEVEL`.

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
- `get_settings()` is `lru_cache`d. In tests, build `Settings(_env_file=None)` directly or call `get_settings.cache_clear()`; the autouse fixture in `server/tests/conftest.py` clears every `ALFRED_*` var and the cache around each test.
- `ALFRED_HOST` defaults to `127.0.0.1`. Listening on all interfaces (`0.0.0.0`) must be set explicitly (e.g. in compose).
- Document every new variable in `infra/.env.example`, with no real values.
- `ALFRED_API_TOKEN` (`SecretStr`) is the shared bearer token the Android app sends. Every route except `/health` must require it: add routes to the `protected` router in `main.py` (or `Depends(require_api_token)`). Unset → protected routes answer 503 (fail closed). FastAPI's `/docs`, `/redoc` and `/openapi.json` are disabled.

### Conventions

- Python: ruff (line length 100, rules E/F/W/I/B/UP/SIM), type hints on public functions, pytest tests next to every feature.
- Kotlin: official code style, Android lint must pass (CI), JVM unit tests (JUnit 4, coroutines-test, MockWebServer) for ViewModels and data classes. Design decisions in `docs/decisions/0002-android-client-stack.md`.
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
| Stuck | **Blocked** | me | — | Dan, then back to the previous stage |

- Move the ticket the moment its stage changes, and comment on the Linear ticket at each transition (what happened, PR link).
- Swap PR labels so a PR has exactly one of `ready-for-review`, `in-review`, `changes-requested` while open.
- A blocker counts as satisfied when it is **Done** or **QA** (the code is merged; only Dan's hardware check remains).
- GitHub GraphQL isn't available in cloud sessions: use the REST API (`gh api repos/Breakwell92/Alfred/...`) for labels, reviews and merges.

### 1. One ticket per session

- Start every ticket in a fresh session (or after `/clear`). Use `/ticket STU-xx` for a specific ticket, or plain `/ticket` to pick up the next free ticket: **Ready, unassigned**, blockers satisfied, highest priority, earliest milestone, runnable in this environment, not overlapping core modules with work already In Development.
- Claim the ticket immediately: assign it to `me` and move it to **In Development** before doing anything else. If it turns out to be assigned already when you go to claim it, another session got there first: pick again.
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
9. Mark the PR as ready for review: add the `ready-for-review` label. This is how `/review` agents find it.
10. Turn on auto-fix for the PR (auto-fix is a per-PR setting, so this is done every time). In a cloud session, start watching the PR's CI checks and review comments and fix clear issues as they arrive. In a local session, tell Dan to run `/autofix-pr` on the branch.
11. Comment on the Linear ticket with the PR link, what changed, and anything left over. Move it to **Code Review**.

**Responding to review feedback** (usually via auto-fix): when a review leaves inline comments, the PR is labelled `changes-requested` and the ticket is back in **In Development**. Fix each clear comment, push, and reply on its thread saying what changed. When every comment is addressed, swap `changes-requested` for `ready-for-review` and move the ticket to **Code Review**. If a comment is ambiguous or architectural, ask Dan and leave the labels as they are.

**Acceptance criteria that need hardware or a human** (real phone, GPU benchmarks, multi-hour soak tests, OAuth sign-in, listening tests): don't claim them. Mark them "Needs Dan to verify" in the PR and the Linear comment, with exact steps to verify.

**If blocked** (missing credentials, a decision only Dan can make, a blocker not done): move the ticket to **Blocked**, comment with what's needed, and stop.

### 4. Running tickets in parallel

- Only work on tickets whose blockers are all **Done** or **QA**. Use `/next` to list them, or start several sessions with plain `/ticket`: each claims a different unassigned ticket. Several `/review` sessions can run at once too: each claims a different PR via the `in-review` label.
- Each parallel ticket gets its own git worktree and its own session, e.g. `git worktree add ../alfred-stu-49 -b <branch> main`. Never share a working directory between sessions.
- Don't run two tickets at once that change the same core modules (router, connector registry, protocol, budget, shared `RequestContext`). If two Ready tickets overlap, do them one after the other.
- Rebase on `main` before opening the PR; resolve conflicts in your own branch.
- Hardware placement: server, model, GPU and Docker tickets run on the home server; Android tickets run where the Android SDK and phone are available.

### 5. Review in a separate session

- Never review or merge your own work in the session that wrote it.
- Reviews run in a fresh session with `/review`, which picks up the oldest open PR labelled `ready-for-review` (or `/review STU-xx` for a specific one) and claims it by swapping the label to `in-review`.
- Reviews start in two ways. When `/ticket` hands a PR off, it starts a fresh local review session itself (`/review STU-xx`, desktop app only). A scheduled local task in the desktop app also runs `/review` every 30 minutes as a backstop: it catches re-reviews after auto-fix (auto-fix turns can't start sessions) and anything the first route missed.
- The reviewer checks the PR against the ticket's acceptance criteria and this file. It never rewrites the code itself.
- **Reviews act as a separate GitHub account** (the reviewer), so review comments aren't authored by the account that opened the PR. `/review` routes every GitHub call through `.claude/scripts/gh-as-reviewer.sh`, which loads the reviewer's token from `~/.config/alfred/reviewer-token` (on Windows, `%USERPROFILE%\.config\alfred\reviewer-token`). Reviews therefore run on a machine that has that file; without it (e.g. cloud sessions) `/review` stops. It also stops if the reviewer account is the PR's author.
- **No feedback** (nothing at all to comment on, CI green, branch mergeable): the reviewer squash-merges the PR, deletes the branch, and moves the ticket to **QA** if it has "Needs Dan to verify" items, otherwise **Done**.
- **Any feedback**: the reviewer posts every finding as its own comment on the PR: inline on a file and line where possible (one review, event `COMMENT`, since the same account can't "request changes" on its own PR), otherwise as a PR conversation comment, never only in the review summary. It then swaps the label to `changes-requested` and moves the ticket to **In Development**. The comments trigger auto-fix on the implementing session. If auto-fix isn't on, `/ticket STU-xx` in a new session resumes from the PR and its comments.
- Merge conflicts don't trigger auto-fix on their own, so a reviewer who finds the branch out of date leaves a comment asking for a rebase, which does.
- Auto-fix acts on clear fixes only; ambiguous or architectural comments come back to Dan.

## Rules that always apply

- Never commit secrets. API keys and tokens go in `.env` (git-ignored); document new variables in `infra/.env.example`.
- Never paste real credentials into code, tests, fixtures, docs or tickets.
- Run the smoke evals (`server/evals`, from STU-60 onwards) before merging changes to prompts, routing or model configuration.
- Safety behaviour (confirmation tiers, privacy flags, kill switch) must not be weakened to make a feature work; raise it with Dan instead.
- Prefer small, reviewable PRs. One ticket, one PR.
