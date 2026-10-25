# 0014: Voice WebSocket gateway

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-64

## Context

The phone (STU-65) streams audio after the wake word and plays back streamed speech. It needs one long-lived, authenticated connection that carries a whole voice turn (audio → STT → router → TTS → audio), confirmations, and later device tools (STU-81) and pushes (STU-84). The target is under 1.5 s from end of speech to first audio for a simple local request on the 3090. STT (ADR 0002), streaming TTS (ADR 0006), the router (ADR 0009) and confirmation gating (ADR 0013) already exist; this ticket joins them up. The protocol itself is in [`docs/protocol.md`](../protocol.md).

## Decision

**One WebSocket per device, `GET /ws/device`, JSON control frames plus binary audio.** Text frames are JSON messages with a `type`, validated by pydantic models (`voice/gateway/protocol.py`). Binary frames are PCM16: raw 16 kHz from the device, and 24 kHz to the device prefixed with `0x01`, a length byte and the turn id. Tagging reply audio lets the phone drop audio from a turn it has cancelled. Device audio isn't tagged, because only one turn is open at a time. Unknown fields are ignored and new message types are additive, so `protocol` (1) only changes for breaking changes. `tool.call`, `tool.result`, `push.notification` and `transcript.partial` are reserved.

**Authentication in the handler.** FastAPI's `HTTPBearer` dependency doesn't run on WebSocket routes, so the socket isn't on the protected router. `DeviceConnection` checks the same `ALFRED_API_TOKEN` itself (`auth.check_api_token`, shared with the HTTP dependency). The token comes from the `Authorization: Bearer` header on the upgrade, or from the first message (`hello.token`) for clients that can't set headers. Nothing but `hello` is read before that. No token configured means close 4503 (fail closed, like the HTTP 503). Wrong or missing means 4401. This is not a new open route.

**Conversations survive reconnects.** `DeviceHub` (on `app.state`) keeps one live connection per `device_id`; a new one replaces the old (closed with 4000). It also maps device → `session_id`, so a phone that reconnects (network change) continues the same Agno session, including a pending confirmation. `hello.session_id` switches conversation. The map is in memory, so a server restart starts new conversations. That's acceptable for one user and keeps state out of Postgres until the action log (STU-93) needs it.

**A turn is a task; the connection keeps reading.** The receive loop never waits on a turn: STT, the agent run and TTS run in a task, so `turn.cancel`, `ping` and audio keep flowing. Sends are serialised with a lock. One turn at a time (`busy` otherwise). Barge-in (STU-72) will cancel and start.

**Pipeline** (`voice/gateway/pipeline.py`):
1. Audio frames go into a queue that `SpeechToText.transcribe` reads as they arrive, so `ALFRED_STT_MAX_AUDIO_S` is enforced mid-utterance. faster-whisper still decodes once at the end, since it has no partials, so `transcript.partial` is reserved.
2. The transcript goes through `chat.answer_pending` first (a pending confirmation), else `Router.route` and `Router.stream`, the same code as `/chat/stream`.
3. Each streamed token is sent as `reply.text.delta` and fed to `LocalTTS.synthesize`, which starts speaking on the first clause or sentence while the model is still writing.
4. The turn ends with `confirm.request` (if it paused), `turn.metadata` (`chat.turn_info` plus timings) and `reply.audio.end`, or with one `error`.

**Timings** are measured from `turn.end` (the end of speech as far as the server can tell): transcript, first token, first audio and total, plus STT decode time and utterance length. They're sent in `turn.metadata`, logged on one INFO line per turn, and added to the stored Agno run's metadata (`metadata["voice"]`, next to `routing`). `python -m alfred_server` now gives `alfred_server.*` loggers a handler. Before this, app INFO lines (routing decisions too) went nowhere.

**Voice confirmations** build on ADR 0013, with the request channel `voice` (`device_type: phone`):
- A paused turn speaks `ask_prompt` ("… Shall I go ahead?") and sends `confirm.request` with its tier, so the app can show buttons.
- The next utterance answers it. `parse_yes_no` catches plain answers; looser speech ("yeah, go on then") goes to `local-router` with a three-way yes/no/other prompt (`voice/gateway/yes_no.py`, an optional `parse` hook on `answer_pending`). A model failure counts as "other", which cancels a pending `write` confirmation: it never approves.
- `confirm.reply` (the app's buttons) is also channel `voice`. It can approve `write`. It can't approve `irreversible`: those are approved on the app's confirmation screen through `POST /confirmations/{id}` with `app_ui`. So the client-asserted `app_ui` flag stays on one route for STU-109 to harden, and the voice socket can't be used to claim it.
- Answers are only accepted for the connection's own conversation.
- A device should send `confirm.reply` after the asking turn's `reply.audio.end`. Sooner gets `busy`, because one turn runs at a time.
- Once an answer starts resuming a run, it runs detached from the turn (`pipeline._detached`, `asyncio.shield`). The approval is already recorded at that point, so cancelling the run would leave an approved action that never ran or was never stored. A disconnect, a reconnect or `turn.cancel` drops only the spoken reply.

**Services per connection, router per turn.** The socket resolves the same dependency providers as `/chat` (agent db, model factory, budget, confirmations; overridable in tests) plus STT/TTS (`None` when `ALFRED_STT_ENABLED` / `ALFRED_TTS_ENABLED` is off, which refuses turns with `voice_unavailable`). Each turn builds a `Router` and checks the agent db first, as `/chat` does per request.

### Measurements (RTX 3090, 2026-10-25)

Server and client on the 3090 box (Windows), compose stack up (`local-router` and `local-main` on Ollama), STT `distil-large-v3` on CUDA (`int8_float16`), default voice Piper `en_GB-alan-medium`. Ten turns per clip with `scripts/ws_client.py --runs 10 --realtime`, each in a fresh conversation. Clips: two made with Piper voices (16 kHz) and the `timer.wav` fixture. All routed to the general agent on `local-router` (simple). Medians in ms from end of speech:

| Clip | Transcript | First token | **First audio** | Total |
| --- | --- | --- | --- | --- |
| "What time is it?" (1.4 s; calls `get_time`) | 211 | 688 | **873** | 1070 |
| "Hello Alfred, how are you today?" (2.9 s) | 274 | 726 | **847** | 1033 |
| `timer.wav` "Set a timer for ten minutes." (2.9 s) | 383 | 767 | **868** | 1016 |

The client's own end of speech → first audio matched the server's within 10 ms (loopback). The target (< 1.5 s) is met with about 600 ms to spare.

Where the time goes:
- STT: 110–530 ms, varying with what else is on the GPU.
- Classifier plus the model's first token: about 450 ms.
- TTS to the first clause: about 100–200 ms. The time question's reply has a `get_time` tool round trip in it too.

The first turn after start-up was slower (about 1.6 s), and the first gated tool call about 6.8 s (cold path: Postgres pool, tool loading). Warm tool-call turns that pause for a confirmation reached first audio in about 1.55 s, because the model writes a tool call, the run pauses and the confirmation is recorded before the prompt is spoken. Not measured: over Tailscale from the phone, and with Kokoro as the default voice.

## Consequences

- STU-65 (the Android client) implements `docs/protocol.md`. STU-81 and STU-84 add their reserved message types, and pushes will reach a device through `DeviceHub.get`.
- Personas (STU-66) will pick the TTS voice per turn; today every turn uses `LocalTTS.default_voice` and `persona_id` only reaches the agent's context.
- A cancelled turn cancels its Agno run mid-flight, except an approved confirmation's resume, which always finishes. Agno may leave a cancelled run stored as running. Nothing reads it today.
- Interim transcripts need a streaming STT engine. The protocol slot exists.
- Latency headroom is about 600 ms on simple requests. Tool calls and confirmations are near the limit. Filler phrases during slow tool calls are STU-67.
- The server only knows when `turn.end` arrived. The phone's VAD (STU-69) adds its end-of-speech delay before that, and should be measured on the phone.
