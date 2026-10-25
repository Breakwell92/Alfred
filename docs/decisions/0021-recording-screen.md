# 0021: Recording screen and partial transcripts

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-115

## Context

Screen 2 of the app design is shown while Dan speaks. It has:

- a REC timer
- a scrolling waveform and an input level in dB
- the noise-filter state and the sample rate
- the transcript as it's being heard, with words the recogniser is still unsure of dimmed
- a stop button: "tap to send, or go quiet"

Push-to-talk (STU-65, [ADR 0017](0017-android-voice-client.md)) streamed audio and ended a turn only on a tap. The gateway ([ADR 0015](0015-voice-gateway.md)) decoded once, at the end, so there was nothing to show while speaking, and `transcript.partial` was only reserved.

## Decision

### Partial transcripts on the server

faster-whisper doesn't stream, so `FasterWhisperSTT` **re-decodes the buffered audio** every `ALFRED_STT_PARTIAL_INTERVAL_S` of new audio (default 0.4 s; `0` turns it off). It sends `transcript.partial {turn_id, stable, unstable}` before `transcript.final`. Some rules keep this from slowing anyone's real transcript:

- At most one partial decode runs per turn.
- A partial starts only when an STT slot is free, so it never queues behind another turn's decode.
- At end of audio, a partial that's still running finishes and is sent (it holds the GPU slot anyway). The final decode then runs as before. The final transcript is therefore delayed by at most the rest of one partial decode.
- A turn that fails or is cancelled sends no more partials. A decode already running is left to finish, so the slot is released cleanly.

The text is split by **LocalAgreement-2** (`split_partial`). Words become stable once two decodes in a row agree on them from the start, compared without case or punctuation. Stable words never change or shrink, so the app can show them bright. The tail after them is `unstable` and drawn dim.

Each partial re-decodes everything heard so far. Over a 30 s cap at 0.4 s steps, that's 75 decodes of growing audio. On the 3090 with `distil-large-v3` a few seconds of audio decodes in roughly 100 ms, so partials keep up. On a CPU they wouldn't: turn them off there with `0`.

`hello.ok` also gains `max_audio_s` (`ALFRED_STT_MAX_AUDIO_S`), so the app cuts a turn just before the server would refuse it. Both additions are optional fields, so there's no protocol version bump.

### End of a turn on the phone

`VoiceController` ends a listening turn in three ways:

- **Stop**, or the close button, which cancels instead.
- **Going quiet**: push-to-talk passes the wake word's `EnergyEndOfSpeech` ([ADR 0019](0019-wake-word.md), an energy threshold over an adaptive noise floor) to `startListening`. Once speech has started, 1 s of quiet sends the turn. For push-to-talk, its no-speech and max-length limits are off: silence before anyone speaks never ends a tapped turn, and the cap below applies. In hold-to-talk, a pause of over a second while still holding sends too. It's a stopgap: Silero VAD (STU-69) replaces it.
- **The max length**: `max_audio_s` minus 0.5 s, at most 60 s, or 30 s if the server didn't say.

Cancel sends `turn.cancel` and never `turn.end`, so nothing reaches the router.

### Levels

The meter uses STU-125's helpers in `AudioLevel.kt` (`pcm16Rms`, `rmsToDbfs`, `dbfsToLevel`, −50…−10 dBFS → 0–1), so the dB reading, the waveform and the orb's `VoiceController.level` agree.

`VoiceController.meter` (`MicMeter`: dBFS, level, sample rate, noise filter, frame count) is a `StateFlow` kept apart from `VoiceState`. Its 50 updates a second redraw only what shows them:

- The waveform reads its bars in the Canvas draw phase.
- The telemetry line is its own small composable.

Each frame's audio is sent before it is measured. `AudioCapture.activeEffects` reports which of NoiseSuppressor and AcousticEchoCanceler were actually enabled for the "NOISE FILTER · ON/OFF" label. A scene label like "RAIN" would need audio classification and is out of scope.

### The screen is a layer, not a destination

`RecordingScreen` is drawn over the whole app while `phase == Listening` (`AlfredApp.kt`), not navigated to. Home stays composed underneath, so a hold-to-talk press that opened the screen keeps its pointer and still sends when released. A Compose pointer stays with the node it went down on, and the layer blocks only new touches. System back cancels.

When the phase leaves Listening, the layer goes and Home shows Thinking and the reply. The Transmitting screen (STU-116) takes over from there.

The voiceprint badge waits for speaker verification (STU-96): the row shows "VOICEPRINT —", and `showVoiceprintMatch` turns the badge on later.

### Screenshot tests

Roborazzi with Robolectric's native graphics renders Compose on the JVM, with no device or emulator, so the test runs in CI's `testDebugUnitTest`. Goldens live in `android/app/src/test/screenshots/`:

- CI passes `-Proborazzi.test.verify=true` and uploads diffs when a test fails.
- `./gradlew recordRoborazziDebug` re-records them after a deliberate change.

## Consequences

- The live transcript and dim tail are only as good as Whisper on partial audio: the last word or two often flip until more audio arrives. That's what the dimming is for.
- Partials cost GPU time while a user speaks. If the 3090 is busy with the LLM, raise the interval or set it to 0.
- `EnergyEndOfSpeech`'s thresholds were tuned for hands-free wake word turns. Dan should check that they suit push-to-talk on the real phone before STU-69 replaces the detector.
- Screenshot goldens are recorded on Windows. If CI's Linux rendering differs slightly, re-record or add a tolerance.
