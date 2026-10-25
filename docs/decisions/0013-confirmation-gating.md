# 0013: Confirmation gating for tools that change things

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-57

## Context

Every connector tool declares a `risk` (ADR 0010): `read` changes nothing, `write` changes something that can be put back (send a message, move an event), `irreversible` can't be undone (money, unlocking doors). Reading should be instant. Anything else needs a yes from Dan, on the channel the request came from, before it runs. This has to exist before any connector that sends or deletes (Google, Home Assistant, Spotify, GitHub). Agents are built per request (ADR 0007), so a paused run has to be continued by a freshly built agent.

## Decision

**Agno's human-in-the-loop pause, our own record.** Gated tools get Agno's `requires_confirmation` flag (`confirmations/policy.py` `gate_agent`, applied by the router to every agent it builds). Agno pauses the run before calling one, and the paused run is stored with the rest of the session. `Router.resume` rebuilds the specialist on the alias it paused on, confirms the run's requirements and calls `acontinue_run`. A cloud alias asks the budget again first and drops to `local-main` if refused. Agno also has an approvals table (AgentOS). We don't use it: we need a summary, tier, channel and expiry per pause, and our own `confirmations` table (`confirmations/store.py`) holds them.

**Tiers.** A tool's tier is Dan's override (`tool_permissions` table, `PUT /settings/permissions/{connector}/{tool}` `{risk}`, `null` resets), else its connector's `default_tier` if it declares one (never above its risk; added in [ADR 0014](0014-spotify-media.md) so ordinary media controls run without asking), else its declared risk. A tool no connector declares is treated as `irreversible` (fail closed). If the overrides can't be read, the request fails (503) rather than run ungated. Cached MCP toolkits are shared across requests, so their flags are rewritten on every request, not only set. Since STU-109 the API can't take a tool out of `irreversible`; only `python -m alfred_server.confirmations set` can (ADR 0016).

**One record per paused run.** It covers every gated call the model made in that turn, joined into one summary at the highest of their tiers. The summary comes from `ToolMeta.confirm_summary` (e.g. `"Send a test message to {recipient}: '{text}'"`, formatted with the call's arguments), falling back to the tool name and arguments.

**Channels.** `device_type` maps to the channel: `text` → `text`, `phone` → `voice`, `scheduled` → `scheduled`.

| Tier | Approved from |
| --- | --- |
| `read` | runs straight away |
| `write` | `text`, `voice` or `app_ui` (never `scheduled`) |
| `irreversible` | `app_ui` only |

`POST /confirmations/{id}` takes `{approve, channel, proof?}`. `channel` defaults to `text`, and only the Alfred app's own confirmation screen sends `app_ui`. An approval counts as `app_ui` only with a `proof` signed by a paired device; without one it's treated as `text`, and a proof that fails is a 403 (ADR 0014, STU-109). The web chat page sends no channel, so it can't approve an irreversible action, which matches its label. A wrong channel gets a 403. An expired or already answered confirmation gets a 409. Claiming an answer is an atomic status update, so two answers can't both resume a run.

**Text replies.** The paused turn's reply asks: "I need your OK first: <summary>. Shall I go ahead?" (irreversible: "... Please approve it in the Alfred app."), and `/chat` and the stream's `done` carry the `confirmation`. While one is pending, the session's next message is checked first:
- A plain yes ("yes", "go ahead", "ok") resumes the run without the classifier.
- A plain no cancels it.
- Anything else cancels a pending `write` confirmation, so a later "yes" can't approve something Dan has stopped thinking about, and is routed as usual. An `irreversible` one stays pending for the app.

**Deny and expiry don't call the model.** The Agno run is marked `cancelled`, which Agno leaves out of later history, so there's no dangling tool call. The reply is fixed and spoken-style ("OK, I won't send a test message to Sam: '…'."). Confirmations expire after `ALFRED_CONFIRMATION_TTL_S` (2 min). The check happens lazily on every read, and a lifespan task (`confirmations/sweeper.py`) also runs it every `ALFRED_CONFIRMATION_SWEEP_INTERVAL_S`.

**Unattended runs** (`scheduled`) are never auto-approved. The run pauses, a `notifications` row is written (push delivery is STU-84), and it waits `ALFRED_CONFIRMATION_SCHEDULED_TTL_S` (1 h) for Dan.

**Demo tool.** The `test_messages` builtin connector's `send_test_message` (`write`) only appends to an in-process list. With `ALFRED_TEST_TOOLS_ENABLED=true` the general (echo) agent has it, so the whole flow can be tried through `/chat` and the web chat page. It's off by default: the smoke evals showed that a second tool and its instruction make `local-router` stop calling `get_time` on time questions (0/20 runs against 7/20 without it). The reply was still right, taken from the request context.

**Evals** run with an in-memory gate: a gated call pauses, is never approved, and is scored as a tool call.

## Consequences

- **`app_ui` is proven, not asserted** (STU-109, ADR 0014). It was originally a client-asserted flag, so any holder of the shared API token could approve an `irreversible` action. Now an `app_ui` approval needs an ECDSA signature from a Keystore key on a device paired with a one-time code from the server CLI (`python -m alfred_server.devices pair`). The shared token alone can't approve an `irreversible` tool.
- The approval is checked against the tier **now** (`ConfirmationService.current_tier`): if Dan tightens a tool while its confirmation waits, the answer must meet the new tier. Relaxing a tool doesn't lower a pending one.

- New connectors only need honest `risk` values and, for gated tools, a `confirm_summary`. The router gates them without further code.
- Voice confirmation (STU-64/65), the app's confirmation screen and push notifications (STU-84) build on this API and the `notifications` table. The voice gateway (ADR 0015) answers with channel `voice`, so it can approve `write` but never `irreversible`, by speech or by its `confirm.reply` message.
- A denied or expired request disappears from the conversation history (the run is cancelled, not completed with a refusal). The model won't remember being told no. This is deliberate: it keeps the history valid and costs no model call.
- Resuming continues on the original alias. If the specialist's tools change between the pause and the answer (connector disabled), the continued run may fail. The answer then gets a 502 and the confirmation stays answered.
