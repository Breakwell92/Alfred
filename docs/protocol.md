# Device ↔ server protocol (WebSocket, version 1)

The phone (and later room satellites) keeps one WebSocket open to the server, over Tailscale. It carries voice turns (audio up, transcript and speech down), confirmations, and later device tools and pushes. Server code: `server/alfred_server/voice/gateway/` (`protocol.py` has the message models). Design and measurements: [ADR 0015](decisions/0015-voice-gateway.md). A reference client: `server/scripts/ws_client.py`.

## Connection

```
GET ws://<server>:8000/ws/device
Authorization: Bearer <ALFRED_API_TOKEN>        (or send the token in `hello`)
```

- **Frames.** Text frames are JSON control messages, each an object with a `type`. Binary frames are audio. Unknown fields are ignored, so either side can add optional fields without a version bump. A new required field or a changed meaning bumps `protocol`.
- **Authentication.** The token goes in the upgrade request's `Authorization: Bearer` header, or, for clients that can't set headers, in `hello.token`. The first frame must be `hello`, sent within `ALFRED_VOICE_AUTH_TIMEOUT_S` (10 s). Nothing else is processed before it. A wrong header token is refused straight away, even if `hello` carries the right one.
- **One connection per device.** A second connection with the same `device_id` replaces the first, which is closed with 4000. Reconnecting is always safe.
- **Conversation.** Each device has a conversation (`session_id`) that survives reconnects, so history and any pending confirmation carry over. `hello.session_id` switches to a given conversation, e.g. to start a new one. The server's device → conversation map lives in memory: after a server restart a device starts a new conversation unless its `hello` names one.
- **Heartbeat.** Send `ping` (or anything) at least every `ALFRED_VOICE_IDLE_TIMEOUT_S` (60 s); a silent connection is closed with 4408. Every 20 s is a good interval. WebSocket-level pings are answered too, but only messages reset the idle timer.

### Close codes

| Code | Meaning |
| --- | --- |
| 1000 | Normal close |
| 4000 | Replaced by a newer connection from the same device |
| 4400 | First frame wasn't a valid `hello`, or unsupported `protocol` |
| 4401 | Missing or wrong token |
| 4408 | No `hello` in time, or idle too long |
| 4503 | The server has no `ALFRED_API_TOKEN` set (it refuses everything) |

## Audio

| Direction | Format | Framing |
| --- | --- | --- |
| Device → server | PCM, signed 16-bit little-endian, **16 kHz**, mono | Raw PCM in each binary frame, any size (20–100 ms is typical). Frames belong to the open turn. |
| Server → device | PCM, signed 16-bit little-endian, **24 kHz**, mono | `0x01`, one byte *n*, *n* bytes of `turn_id` (ASCII), then PCM. At most 200 ms of audio per frame. |

Reply audio carries its turn id so the device can drop audio from a turn it has cancelled. Audio sent with no open turn (late frames after `turn.end`, a cancel or a failed turn) is dropped silently.

## Client → server

| Type | Fields | Notes |
| --- | --- | --- |
| `hello` | `protocol` (1), `device_id`, `device_type` (`"phone"`), `app_version`?, `capabilities`? (strings), `token`?, `session_id`? | First frame. Answered by `hello.ok`. |
| `ping` | `t`? (number) | Answered by `pong` with the same `t`. |
| `turn.start` | `turn_id`, `sample_rate` (16000), `persona_id`?, `context`? `{active_room?, location?}` | Opens a turn. Audio frames follow. One turn at a time. |
| `turn.end` | `turn_id` | End of speech: the server transcribes and answers. |
| `turn.cancel` | `turn_id` | Stops the turn wherever it is (listening, thinking or speaking). Answered by `turn.cancelled`; nothing if it had already finished. |
| `confirm.reply` | `confirmation_id` (at most 56 characters), `approve` (bool), `turn_id`? | The app's yes/no buttons for a `confirm.request`. Its spoken answer is a turn tagged `turn_id`, default `confirm-<confirmation_id>`. |

`turn_id`: 1–64 of `A–Z a–z 0–9 . _ : -`. `device_id`, `session_id`, `persona_id`: the same characters. `context` strings: one line, at most 100 characters, no `<` or `>` (they go into the agent's prompt).

## Server → client

| Type | Fields | Notes |
| --- | --- | --- |
| `hello.ok` | `protocol`, `server_version`, `session_id`, `input_sample_rate`, `output_sample_rate`, `voice` | `voice` is false when the server has STT or TTS off; turns are then refused. |
| `pong` | `t` | |
| `transcript.final` | `turn_id`, `text`, `language`, `audio_s` | What was heard. |
| `reply.text.delta` | `turn_id`, `text` | Reply text as it's generated (for captions). |
| `reply.audio.start` | `turn_id`, `sample_rate` (24000), `encoding` (`pcm_s16le`), `channels` (1) | Before the first audio frame of a turn. |
| *(binary)* | | Reply audio, see above. |
| `confirm.request` | `turn_id`, `id`, `summary`, `tier` (`write` / `irreversible`), `expires_at` | The reply asked for an OK before running a tool. See below. |
| `turn.metadata` | `turn_id`, `specialist`, `alias`, `tier` (`local` / `cloud`), `reason`, `budget_code`, `budget_tier`, `tools`, `input_tokens`, `output_tokens`, `cost_gbp`, `timings` | How the turn ran. Routing fields are absent when no agent ran (e.g. "No" to a confirmation). |
| `reply.audio.end` | `turn_id` | **Last message of a successful turn.** |
| `turn.cancelled` | `turn_id` | Last message of a cancelled turn. |
| `error` | `turn_id`?, `code`, `message` | With a `turn_id` it's the last message of that turn. Without one, the connection carries on. |

`timings` (milliseconds from when the server got `turn.end`, the end of speech as far as it can tell): `transcript_ms`, `first_token_ms`, `first_audio_ms`, `total_ms`, plus `stt_ms` (decode time) and `audio_s` (utterance length). The same timings are logged and stored in the Agno run's metadata (`metadata["voice"]`).

### Error codes

| Code | When |
| --- | --- |
| `bad_message`, `unsupported` | A frame the server can't read, or a reserved type. The connection carries on. |
| `voice_unavailable` | STT or TTS is off on the server. |
| `busy` | `turn.start` / `confirm.reply` while another turn is in progress. |
| `bad_sample_rate`, `bad_audio` | Audio isn't 16 kHz PCM16. |
| `audio_too_long` | The utterance passed `ALFRED_STT_MAX_AUDIO_S`. Sent as soon as it happens, before `turn.end`. |
| `no_speech` | Nothing recognised. |
| `stt_failed`, `tts_failed` | A speech engine failed. With `tts_failed`, the reply text was still sent. |
| `agent_failed` | The model run failed. |
| `storage_unavailable`, `confirmations_unavailable`, `gateway_unavailable` | Postgres or the LiteLLM gateway can't be reached or isn't configured. |
| `unknown_confirmation`, `confirmation_closed` | `confirm.reply` for a confirmation that isn't this conversation's, or was already answered or has expired. |
| `internal` | Anything else (logged on the server). |

## A turn

```
→ turn.start {turn_id: "t1"}
→ <PCM> <PCM> …
→ turn.end {turn_id: "t1"}
← transcript.final {text: "What time is it?"}
← reply.text.delta {text: "It's "} …
← reply.audio.start {sample_rate: 24000}
← <0x01 2 "t1" PCM> …                    (speech starts on the first sentence or clause)
← turn.metadata {specialist, alias, …, timings}
← reply.audio.end {turn_id: "t1"}
```

Text and audio interleave: TTS starts on the first sentence (or clause) while the model is still writing.

## Confirmations

Tools that change things are gated ([ADR 0013](decisions/0013-confirmation-gating.md)). When a turn reaches one, the spoken reply asks ("I need your OK first: Send a test message to Sam: '…'. Shall I go ahead?") and the turn sends `confirm.request` before `turn.metadata`.

- **`write`** can be answered by voice: the next turn's transcript is checked first. A plain "yes"/"no" is matched directly; anything looser ("yeah, go on then") is classified by `local-router`. A yes runs the tool and speaks the result; a no is spoken as "OK, I won't …". Anything else (a new request, or the check failing) cancels the pending confirmation and is answered as a normal request. The app's buttons can send `confirm.reply` instead.
- **`irreversible`** can't be approved by voice or `confirm.reply`: the server says "That one needs approving in the Alfred app." and sends the `confirm.request` again. The app approves it on its confirmation screen with `POST /confirmations/{id}` `{"approve": true, "channel": "app_ui"}` (STU-109 will bind that to the app).
- Pending confirmations expire after `ALFRED_CONFIRMATION_TTL_S` (2 min).
- Once a confirmation is approved, the action runs to completion and is stored in the conversation even if the turn is cancelled (`turn.cancel`, a disconnect, or a reconnect replacing the connection). Only the spoken reply is lost.

## Reserved

Not sent or accepted yet (`unsupported` if received), kept for later tickets:

- `transcript.partial` (server → device): interim transcripts, once the STT engine streams them.
- `tool.call` / `tool.result`: tools that run on the device (STU-81).
- `push.notification`: server-initiated messages (STU-84).
