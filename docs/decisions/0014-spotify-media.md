# 0014: Spotify connector and the Media specialist

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-74

## Context

Dan wants to control Spotify by voice and text: play an artist or playlist on a named speaker, pause, skip, change the volume, ask what's playing. Spotify's Web API does playback control (Premium only) against the account's "devices". This is also the first specialist whose tools come from the connector registry (ADR 0010), and the first connector whose tools change things but shouldn't ask first: ADR 0013 pauses every `write` tool for a yes, and "Shall I go ahead?" before every pause would make the feature useless.

## Decision

**Our own async toolkit over the Web API** (`connectors/builtin/spotify.py`, httpx). Agno's `SpotifyTools` is sync and has no pause, skip, volume, devices, transfer or queue. Community Spotify MCP servers cover parts of it, but device targeting by spoken name is the hard bit and is easier to get right (and test) ourselves. Fourteen `spotify_*` tools: four `read` (now playing, devices, search, my playlists) and ten playback controls (play, pause, next, previous, volume, shuffle, repeat, transfer, queue, like).

- Each tool returns a short sentence for the agent to say, including for errors, and never raises: no active device, Premium required, signed out (401), rate limited (429), unreachable.
- Device names are matched loosely (`match_device`): case, punctuation and filler words ("the", "speaker") ignored, then substring, shared words, then close spelling (difflib ≥ 0.6). An unknown name gets "I can't see a Spotify device called X. There's …" and nothing plays.
- `spotify_play(query, kind, device)` looks in Dan's own playlists first (so "Discover Weekly" or "my running playlist" works), then searches. Without a kind it plays the artist if the top artist matches the query closely, else the top track. No query resumes.
- OAuth through the generic flow (ADR 0010) with PKCE; Spotify accepts the client secret in the body and may omit a new refresh token, which `oauth.py` already handles.

**`ToolMeta.default_tier`.** A connector can declare a tool's confirmation tier below its risk: Spotify's controls are `risk="write"` (honest: they change playback) with `default_tier="read"`. The tier is now Dan's override, else `default_tier`, else `risk`. The registry rejects a `default_tier` above `risk`, so it can only relax, and only in code review. `GET /settings/permissions` shows `declared`, `default`, `override` and `effective`; Dan can tighten any control with `PUT /settings/permissions/spotify/<tool> {"risk": "write"}`.

**Connector tools reach agents through the router.** `Specialist(..., uses_connectors=True)` marks a specialist whose tools come from connectors. The router takes a `tools` loader (`ConnectorService.load_tools` in `/chat`) and, for such a specialist, appends the loaded tools to the factory's agent before `gate_agent`, so connector tools are gated like any other. A loader failure is logged and the agent runs without them (its instructions say Spotify isn't connected). Specialist factories stay synchronous and unchanged. `general` doesn't set the flag: it has its own `get_time`, and loading the `time` connector too would duplicate it.

**Media specialist** (`agents/media.py`): short spoken confirmations, device names passed as spoken, plain error relays. Plex and TV playback join it later. Its classifier description mentions moods ("music for a mood"): without that, "put on something relaxing for dinner" went to `general`.

**Evals** load every toolkit connector of a specialist with empty credentials (`MockToolLoader`), since tool calls are answered from fixtures and the real API is never called. `cases/media.yaml`: 8 cases, 5 in the smoke set.

## Consequences

- Connector tickets (Google, Home Assistant, GitHub…) register a specialist with `uses_connectors=True` and get their tools gated without router changes. Home Assistant (STU-62) can use `default_tier` for ordinary device control.
- `default_tier` relaxes a safety default in code, so a reviewer must check each use is ordinary, reversible control.
- Real playback needs Premium and a running Spotify client: the API can only target devices that are listed (the app open, or a Connect speaker awake).
- Spotify restricts its own algorithmic playlists (Discover Weekly, Release Radar) for newer API apps. If they don't appear in `/me/playlists`, "play my Discover Weekly" falls back to search and may not find it (see `docs/connectors/spotify.md`).
- Rooms (STU-78) will let "play music in here" resolve the device from `RequestContext.active_room`.
