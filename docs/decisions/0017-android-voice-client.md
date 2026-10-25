# 0017: Android voice client (push-to-talk)

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-65

## Context

The server's device WebSocket (`/ws/device`, [ADR 0015](0015-voice-gateway.md), `docs/protocol.md`) is in place. The app needs to talk to it: hold a socket open, send mic audio, play the streamed reply, and answer confirmations. Push-to-talk comes first. The wake-word service (STU-68) and follow-up mode (STU-72) come later and must reuse the same pieces rather than grow their own.

## Decision

**Layers** (`android/app/src/main/java/com/alfred/app/data/voice/`), each behind an interface so it can be faked in JVM tests:

- `Protocol.kt`: message classes and parsing with org.json (already used for HTTP), reply-audio frame parsing. No serialisation library: the protocol is small and its JSON is checked in `ProtocolTest`.
- `DeviceConnection` (`OkHttpDeviceConnection`): one app-wide socket. Token in the `Authorization` header (never in `hello`); `hello` with a persistent random `device_id` (`DeviceIdRepository`, DataStore). It exposes `state` (for the UI) and one ordered `events` flow (messages, reply audio, `Disconnected`).
- `AudioCapture` (`AudioRecordCapture`): 16 kHz mono PCM16, `VOICE_RECOGNITION` source (no AGC on most phones, which STT prefers), 20 ms frames, `NoiseSuppressor` and `AcousticEchoCanceler` when the phone has them. A cold `Flow`: the mic is open only while collected.
- `AudioPlayer` (`AudioTrackPlayer`): streaming `AudioTrack` (`USAGE_ASSISTANT`, speech) fed from one background thread so blocking writes never touch the socket or UI threads. Playback starts once 150 ms is buffered (or the reply ends), which absorbs network jitter between 200 ms frames without adding noticeable delay. Other audio is ducked (`AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`) and a connected Bluetooth A2DP/LE output is preferred.
- `VoiceController`: the turn state machine (Idle → Listening → Thinking → Speaking). It drops any message or audio not for the current turn, so a cancelled turn's late audio is never played.

**Connection lifecycle.** The socket is open while the app is on screen (`MainActivity` `onStart`/`onStop`, kept through rotation). It restarts when the server URL or token changes. Failures retry after 1, 2, 4, 8 then every 15 s, reset after a successful `hello.ok`; "Talk" retries at once. Close 4401/4503/4400 (or an HTTP 401/403/503 upgrade) stop retries until settings change or Talk is pressed, since retrying can't fix them. The app sends `ping` every 20 s (the server's idle timeout is 60 s) and OkHttp WebSocket pings every 15 s spot a dead link after a network change.

**Talk button.** One button, two modes: press and hold (≥ 400 ms) sends on release; a shorter tap starts and a second tap sends. A tap while thinking or speaking stops (`turn.cancel` if the server is still working; playback stops either way). No mode setting needed.

**Confirmations.** A `confirm.request` opens a bottom sheet.
- `write`: Approve/Deny send `confirm.reply` on the socket with our own `turn_id`, so the spoken answer plays as its own turn. If the asking turn hasn't finished on the server, its playback stops and the reply waits for the turn's last message (the server would answer `busy`). Saying yes/no works too; starting a turn closes the sheet.
- `irreversible`: the server refuses `confirm.reply` approvals, so the sheet uses `POST /confirmations/{id}` with a signed proof ([ADR 0016](0016-app-approval-signing.md)). An unpaired phone is told to pair in Settings. The answer's text is shown, not spoken (the HTTP path has no audio).
- The sheet closes when the confirmation expires (`expires_at`).

## Consequences

- The wake-word service (STU-68, [ADR 0019](0019-wake-word.md)) reuses `DeviceConnection`, `AudioCapture`, `AudioPlayer` and `VoiceController`. Its foreground service holds the socket open while the app is in the background, through `LinkHolds`. The socket now closes in `onStop` only if nothing else holds it.
- Barge-in (STU-72) can build on `cancel()` and the echo canceller already attached to the mic.
- Replies approved over HTTP aren't spoken. If that matters, the server could send the answer as a turn on the device's socket.
- Bluetooth routing covers A2DP/LE audio; SCO (call-mode headsets) isn't set up, so a mono car kit may not be used for the reply.
