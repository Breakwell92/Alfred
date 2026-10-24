# Architecture

Alfred is a personal, wake-word-activated Android voice assistant. Personal use only.

- **Android client (thin):** wake word, audio capture and playback, phone actions (SMS, alarms, torch, …).
- **Home server** (RTX 3090 box; services in Docker Compose, see `docs/infra.md`):
  - Postgres (with pgvector) for sessions, memory, schedules, connector registry and logs
  - Ollama serving local models on the GPU (`docs/decisions/0003-local-models.md`)
  - speech-to-text (`alfred_server/voice/stt.py`, faster-whisper on CUDA; ADR 0002) and text-to-speech
  - local LLMs, served through LiteLLM alongside cloud models
  - an Agno agent layer (router plus specialist agents)
  - connectors: Google, Home Assistant, Spotify, Plex, Slack, GitHub, Samsung TVs
  - a scheduler for timed and event-triggered tasks
- **Cloud LLMs** (Claude, GPT, Gemini) are used only when enabled, under budget, and needed.

The client and server talk over a WebSocket (see `docs/protocol.md` once it exists).

## Repository layout

| Path | Contents |
| --- | --- |
| `server/` | Python package `alfred_server` (FastAPI + Agno) |
| `android/` | Android app (Kotlin + Compose) |
| `infra/` | Docker Compose stack and `.env.example` |
| `docs/` | Architecture, protocol, connectors, infra notes and ADRs (`docs/decisions/`) |
