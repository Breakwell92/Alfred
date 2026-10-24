# Architecture

Alfred is a personal, wake-word-activated Android voice assistant. Personal use only.

- **Android client (thin):** wake word, audio capture and playback, phone actions (SMS, alarms, torch, …).
- **Home server** (RTX 3090 box; services in Docker Compose, see `docs/infra.md`):
  - Postgres (with pgvector) for sessions, memory, schedules, connector registry and logs
  - Ollama serving local models on the GPU (`docs/decisions/0003-local-models.md`)
  - speech-to-text (`alfred_server/voice/stt.py`, faster-whisper on CUDA; ADR 0002) and text-to-speech (`alfred_server/voice/tts.py`, Kokoro on CUDA with Piper on CPU as fallback, streamed sentence by sentence as 24 kHz PCM16; ADR 0006)
  - local LLMs, served through LiteLLM alongside cloud models
  - an Agno agent layer (router plus specialist agents)
  - connectors: Google, Home Assistant, Spotify, Plex, Slack, GitHub, Samsung TVs
  - a scheduler for timed and event-triggered tasks
- **Cloud LLMs** (Claude, GPT, Gemini) are used only when enabled, under budget, and needed. The budget service (`alfred_server/budget/`, ADR 0008) answers "may this call use cloud alias X?" from Dan's monthly cap and LiteLLM's spend logs; cloud is off until enabled with `PUT /settings/budget`.

The client and server talk over a WebSocket (see `docs/protocol.md` once it exists), reached over Tailscale (no exposed ports).

**Auth:** the app sends a shared bearer token (`ALFRED_API_TOKEN`) on every request; the server requires it on every route except `GET /health`, and refuses everything (503) when no token is configured. The app shows connection status by polling `/health` and the authenticated `/me`. See `docs/decisions/0004-android-client-stack.md`.

## Repository layout

| Path | Contents |
| --- | --- |
| `server/` | Python package `alfred_server` (FastAPI + Agno) |
| `android/` | Android app (Kotlin + Compose) |
| `infra/` | Docker Compose stack and `.env.example` |
| `docs/` | Architecture, protocol, connectors, infra notes and ADRs (`docs/decisions/`) |
