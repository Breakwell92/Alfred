# Alfred

Personal, wake-word-activated Android voice assistant. A thin Android client (wake word, audio, phone actions) talks to a home server (RTX 3090) running speech-to-text, text-to-speech, local LLMs via LiteLLM, an Agno agent layer, connectors (Google, Home Assistant, Spotify, Plex, Slack, GitHub, Samsung TVs) and a scheduler. Cloud LLMs are used only when enabled, under budget and needed. Personal use only.

- Repo: https://github.com/Breakwell92/Alfred (default branch `main`)
- Tickets: Linear, team **Studio**, project **Alfred** (issue keys `STU-…`)

> STU-47 adds the repo layout, tooling and test/lint commands below the "Project layout" heading. Extend this file; don't replace the workflow rules.

## Project layout

_To be filled in by STU-47 (server/, android/, infra/, docs/), then kept current by every ticket that changes layout, commands or conventions._

## Working on tickets

### 1. One ticket per session

- Start every ticket in a fresh session (or after `/clear`). Use `/ticket STU-xx` to run the standard workflow, or plain `/ticket` to pick up the next unblocked ticket automatically (highest priority, earliest milestone, runnable in this environment, not overlapping work already In Development). The picked ticket is claimed by moving it to **In Development** straight away.
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

Linear statuses for this team: **Ready → In Development → Code Review → Done**, plus **Blocked**.

1. Read the ticket and every ticket listed as blocking it (they define the interfaces you build on). Check blockers are **Done**; if not, stop and say so.
2. Move the ticket to **In Development**.
3. Create the branch Linear generates for the ticket (`gitBranchName`, e.g. `dpbreakwell/stu-48-…`) from up-to-date `main`.
4. Plan first for anything non-trivial (plan mode). Keep to the ticket's scope and "Out of scope" section.
5. Implement with tests. Follow existing patterns before inventing new ones.
6. Verify: lint, unit tests, and any evals the ticket names. Fix failures; don't skip or delete tests to get green.
7. Update the shared-memory docs above if affected.
8. Commit using conventional commits (`feat(router): …`, `fix(tts): …`) referencing the ticket key. Push and open a PR titled `STU-xx: <ticket title>` with: summary, how it was tested, acceptance criteria checklist (ticked or marked "needs Dan"), follow-ups.
9. Turn on auto-fix for the PR (auto-fix is a per-PR setting, so this is done every time). In a cloud session, start watching the PR's CI checks and review comments and fix clear issues as they arrive. In a local session, tell Dan to run `/autofix-pr` on the branch.
10. Comment on the Linear ticket with the PR link, what changed, and anything left over. Move it to **Code Review**.

**Acceptance criteria that need hardware or a human** (real phone, GPU benchmarks, multi-hour soak tests, OAuth sign-in, listening tests): don't claim them. Mark them "Needs Dan to verify" in the PR and the Linear comment, with exact steps to verify.

**If blocked** (missing credentials, a decision only Dan can make, a blocker not done): move the ticket to **Blocked**, comment with what's needed, and stop.

### 4. Running tickets in parallel

- Only work on tickets whose blockers are all **Done**. Use `/next` to list them, or start several sessions with plain `/ticket`: each claims a different ticket.
- Each parallel ticket gets its own git worktree and its own session, e.g. `git worktree add ../alfred-stu-49 -b <branch> main`. Never share a working directory between sessions.
- Don't run two tickets at once that change the same core modules (router, connector registry, protocol, budget, shared `RequestContext`). If two Ready tickets overlap, do them one after the other.
- Rebase on `main` before opening the PR; resolve conflicts in your own branch.
- Hardware placement: server, model, GPU and Docker tickets run on the home server; Android tickets run where the Android SDK and phone are available.

### 5. Review in a separate session

- Never merge your own work in the same session that wrote it.
- Reviews use `/review STU-xx` in a fresh session. The reviewer checks the PR against the ticket's acceptance criteria and this file, and reports findings; it doesn't rewrite the code itself unless asked.
- Review findings posted as PR comments are picked up by auto-fix on the implementing session when it's on. Otherwise fix them in the implementing branch (a new session is fine: `/ticket STU-xx` resumes from the PR).
- Auto-fix acts on clear fixes only; ambiguous or architectural review comments come back to Dan.
- Dan merges. After merge, the ticket moves to **Done**.

## Rules that always apply

- Never commit secrets. API keys and tokens go in `.env` (git-ignored); document new variables in `infra/.env.example`.
- Never paste real credentials into code, tests, fixtures, docs or tickets.
- Run the smoke evals (`server/evals`, from STU-60 onwards) before merging changes to prompts, routing or model configuration.
- Safety behaviour (confirmation tiers, privacy flags, kill switch) must not be weakened to make a feature work; raise it with Dan instead.
- Prefer small, reviewable PRs. One ticket, one PR.
