# 0021: Voice-defined routines (instant macros, no LLM at runtime)

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-87

## Context

"When I say movie time, dim the lounge lights and turn on the TV." Dan wants to say a phrase and have a fixed set of actions happen at once. Going through the router means a classifier call and a specialist run on every use: about a second or more, and a model that might do something slightly different each time. Steps saved as concrete tool calls can run in a few hundred milliseconds, cost nothing, and do the same thing every time.

The hard parts:
- creating a routine (turning a description into concrete calls) without acting on it
- deciding which tools may run unattended
- matching the phrase without stealing ordinary requests from the router

## Decision

**A routine is data.** It has:
- a name
- 1–10 trigger phrases
- 1–20 steps `{connector, tool, args, delay_s, stop_on_error}`
- an optional reply ("Enjoy the film."; the default is the name)
- `enabled`
- `approved_tools`

It's stored as JSON in the Postgres `routines` table (`alfred_server/routines/`).

**Matched before the router, after confirmations.**
- `/chat`, `/chat/stream` and the voice gateway first let a pending confirmation take a "yes"/"no", then ask `RoutineService.match_and_run`, then route.
- The matcher (`routines/matcher.py`) uses no model. It normalises the text: case, punctuation, a leading "hey Alfred"/"Alfred", "please".
- It matches only the **whole utterance**:
  - a phrase exactly, or "run/start/do/activate <phrase> [routine]"
  - otherwise a `difflib` similarity ≥ 0.9 to a phrase whose word count is within one, for STT slips like "movie times".
- "Movie time tomorrow at 8" or "make a movie time routine" go to the router.
- When two routines are equally close, neither runs.
- Phrases are unique across routines and can never be a plain yes/no.
- If the routines can't be read, the request is routed as usual: routines are a fast path, not a gate.

**Run directly on the connector tool layer** (`routines/runner.py`):
- Each step's tool is taken from the same loader the router uses (`ConnectorService.load_tools`), so disabled or disconnected connectors' steps fail ("Home Assistant isn't connected.").
- Each tool is called through Agno's `FunctionCall`, which validates the arguments.
- Steps run in order, waiting `delay_s` first.
- A failed step is reported by number and description ("Step 2 (turn on media_player TV) failed: Home Assistant isn't reachable right now.") and the rest still run, unless that step has `stop_on_error`.
- A tool that never raises reports failure by returning a `ToolError` (a `str` subclass in `connectors/models.py`). The Home Assistant and Spotify toolkits do so on every failure path, and agents see the same sentence as before.
- Each run logs `routine '<name>' ran N of M steps in X ms (no LLM, trigger <device>)`. The result is the turn's `turn` / `turn.metadata` (`specialist: "routine"`, no model, no tokens, the steps and timing).

**What a routine may hold** (`routines/validation.py`, checked when saved and again before every step runs):
- Only tools their connector declares.
- Never an `irreversible` tool, by declared risk or by Dan's override. Those always need a signed approval in the app (ADR 0013, 0016).
- A gated (`write` tier) tool only if it's in the routine's `approved_tools`.
- Tools that already run without asking (`read` tier, including `write` tools with `default_tier="read"`: lights, heating, scenes, music) need no approval.
- If Dan later raises a tool's tier, the step is blocked at run time and reported; the rest of the routine still runs.

**Standing approval for gated steps.** As the ticket specifies, approving a routine's read-back is Dan's explicit approval for its `write`-tier steps. They then run without asking whenever the routine runs. The approval is scoped to that routine and those tools. It's the same trust level as `PUT /settings/permissions` relaxing a `write` tool, which the API token can already do. It can never cover `irreversible` tools.

The REST API can set `approved_tools` directly (same reasoning). The voice path sets it only through the confirmed `save_routine` call.

**Creating by voice: the `routines` specialist** (`agents/routines.py`):
- It gets the `routines` connector's tools:
  - `list_step_tools`: connected tools, their argument schemas and whether they ask first; `irreversible` tools are left out
  - `save_routine`
  - `list_routines`
  - `delete_routine`
- It also gets the **read-risk** tools of the home, media and general connectors, for looking up real names (`ha_get_state`, `spotify_search`, …). `Specialist.read_only_sources` makes the router load those specialists' connector tools and drop everything whose declared *risk* isn't `read`. So the specialist can't dim the lights while creating "movie time", even though `ha_set_light` normally runs without asking.
- `save_routine` is `write`, so the run pauses for the existing confirmation flow. Its summary (`ToolMeta.summarise`, a callable used when a template can't express it) reads back every step and flags those that will run without asking each time: "Save the routine 'movie time', run by saying 'movie time': set the lounge lights and turn on media_player TV".
- On approval the step arguments are checked against each tool's schema, then the routine is saved; saving a name that already exists replaces that routine.

**REST**, on the protected router: `GET/POST /routines`, `GET/PUT/DELETE /routines/{id}`, `POST /routines/{id}/run` (the app's Run button, trigger `api`). `RoutineService.run_by_id(id, trigger)` is the hook for schedules and triggers.

## Consequences

- "Movie time" runs in roughly the time the tool calls take (one HA round trip per step), with no model call.
- Routine runs aren't stored in Agno's conversation history: the next agent turn doesn't know a routine just ran. That's acceptable for instant macros. The action log (STU-93) should record them.
- A routine saves exact arguments. If a device is renamed in HA, the step fails and says so; Dan recreates or edits it.
- Routines that use MCP-based connectors work at run time, but creating them by voice can't look names up through MCP tools: those toolkits are shared across requests, so they're left out in read-only mode. None exist yet.
- The `routines` connector must be enabled like any other (`PUT /connectors/routines {"enabled": true}`).
- Adding a specialist changes the classifier prompt, so the full eval set was rerun (`evals/cases/routines.yaml` adds the creation, listing and deletion cases). `local-router` turned out to be sensitive to where it sits: listed last, it sent "something relaxing for dinner" to home and "activate movie time" to routines. Listed second (after `general`), every case routes as before.
- Follow-ups:
  - the app's Routines screen (list, Run, edit/delete)
  - triggering routines from schedules and event triggers (STU-82, STU-86)
  - a Plex "open Plex" step once that connector exists (STU-75)
