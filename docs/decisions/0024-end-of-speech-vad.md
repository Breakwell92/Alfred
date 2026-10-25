# 0024: End of speech with Silero VAD

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-69

## Context

A wake word turn has no button to release, so the phone has to hear when the speaker has finished. Push-to-talk's "or go quiet" ([ADR 0021](0021-recording-screen.md)) needs the same thing. Until now both used `EnergyEndOfSpeech` ([ADR 0019](0019-wake-word.md)), an RMS threshold over an adaptive noise floor. It's cheap, but it can't tell speech from a TV or a clatter, and it needed a full second of quiet to be safe. The ticket asks for:
- Silero VAD on the phone, on the ONNX Runtime the wake word already uses;
- about 700 ms of silence to end a turn;
- a quiet cancel when nobody speaks within 5 s;
- a 30 s cap;
- tuning on a hidden developer screen;
- a log line per turn.

## Decision

**Model: Silero VAD v5.1.2** (`silero_vad.onnx`, MIT, 2.3 MB). It's in `app/src/main/assets/vad/`, with the licence and SHA-256 in `android/licenses/silero-vad/`. We use v5 rather than the older v4 file shipped with openWakeWord because v5 is Silero's current model and is better in noise. The model:
- takes 512-sample (32 ms) chunks at 16 kHz, scaled to ±1;
- needs the previous chunk's last 64 samples in front of each chunk, as Silero's own wrapper does, so each input is `[1, 576]`;
- carries a `[2, 1, 128]` recurrent state from chunk to chunk;
- takes `sr = 16000`.

`OnnxSileroVad` creates the session once, single-threaded, and the whole app shares it. Each turn gets its own `VadStream` (state, context, partly filled chunk), which turns the mic's 20 ms PCM16 frames into chunk probabilities. On desktop CPU a chunk takes 0.14–0.23 ms. On the phone it's probably under 1 ms per 32 ms, so it runs on the frame collector's thread (main) like the energy endpointer did.

**Turn state machine** (`EndpointStateMachine`, pure, tested with synthetic scores):
- **Listening → Speaking** once chunks at or above the threshold (0.5) have lasted `minSpeechMillis` (250 ms) in a row, so a click, a cough or the chime's echo doesn't start the countdown.
- **Speaking**: a chunk counts as quiet only below threshold − 0.15. That's Silero's own hysteresis: a soft consonant or a trailing vowel at 0.4 keeps the speech going.
- **End** after `endSilenceMillis` (700 ms) of quiet since the last speech chunk, or at `maxUtteranceMillis` (30 s).
- **NoSpeech** if speech hasn't started by `noSpeechMillis` (5 s).

`EndOfSpeechDetector.onFrame` now returns an `Endpoint`: `Continue`, `End` or `NoSpeech`. It used to return a Boolean.

**No speech is a quiet cancel.** On `NoSpeech`, `VoiceController` cancels the turn: `turn.cancel`, never `turn.end`. Nothing is transcribed, no error is shown, and the `onNoSpeech` callback runs. The wake word loop uses it to play a soft chime (`WakeFeedback.onNoSpeech`: `TONE_PROP_NACK` at about half the wake chime's volume, only when the chime is on), then goes back to listening. Before this, a silent wake turn sent silence to the server and showed "Didn't catch that".

**Push-to-talk** (and CUT IN on the Transmitting screen, [ADR 0023](0023-transmitting-screen.md)) uses the same Silero detector with the no-speech and length limits off. The button is waiting, and `VoiceController` caps the length from `hello.ok`. ADR 0021's rule still holds: while the button that started a hold-to-talk turn is down, silence never ends the turn. The hold is part of the detector (`pushToTalk(held)`; `EndpointStateMachine.onChunk(…, held)`): while held it keeps tracking speech but neither ends nor reports, and the quiet countdown starts again from the release. So the log never shows an end that didn't happen.

**Fallback.** `EndOfSpeechFactory` (a Koin single) loads the model on `Dispatchers.Default` when it's first built. That happens when Home or the wake word service first needs it. Until the model is ready, or if it can't load, a turn uses `EnergyEndOfSpeech` with the same no-speech and length limits. "Can't load" covers a missing asset and ONNX Runtime not built for the ABI (`LinkageError`). The developer screen also has a switch to use the energy endpointer, for comparison. `EnergyEndOfSpeech` now returns `NoSpeech` too.

**Pre-roll: none, on purpose.** The ticket asked for about 300 ms of ring-buffered pre-roll so the first word isn't clipped. That matters when the VAD gates what is sent, and here it doesn't: `VoiceController` streams every frame from the moment the mic opens, and the VAD only decides when to stop. The real gap is elsewhere. After the wake word, the wake recording ends, the 150 ms chime plays, and then the turn's recording opens. ADR 0019 chose that order so the chime isn't sent. A ring buffer from the wake recording would only add the tail of "Jarvis" to the request. If Dan finds first words clipped when he speaks straight after the wake word, the fix is to keep one recording open through the chime and hand its frames to the turn (STU-128).

**Tuning and logging.**
- Tapping the Settings title 7 times shows **Developer: end of speech**, with:
  - which endpointer is in use;
  - the Silero switch;
  - sliders for threshold, end silence, no-speech time, longest wake word turn and speech needed to start;
  - "Reset to defaults";
  - the last 10 turns.
- The settings are `VadSettings` in the app's DataStore under `vad_*` keys, each clamped to the slider's range.
- Every turn the detector ends itself is logged, Silero or energy fallback (`engine` says which), to logcat (`Alfred/VAD`) and appended to `files/endpoint_events.jsonl`. Each record has the mode, reason, total ms, speech start, last speech, `latency_ms` (from the last speech chunk to the decision), µs per chunk and the settings used.
- Read the log with `adb shell run-as com.alfred.app cat files/endpoint_events.jsonl`.

The developer controls live on the Settings screen itself rather than as a separate destination. The drawer lists every `Destination`, so a separate screen would have needed special-casing to stay hidden.

## Results

JVM tests with the real model (`OnnxSileroVadTest`, desktop ONNX Runtime; it runs in CI and is skipped on the JBR on Windows like the wake word models). The input is the server's speech fixtures with their own silence trimmed, fed in 20 ms frames:

| Case | Result |
| --- | --- |
| "What is the weather … tomorrow morning, and will I need an umbrella?" (pause at the comma) | One turn. Ended 704 ms after the last speech chunk. |
| "Turn off the lights …" + 450 ms gap + "Set a timer for ten minutes." | One turn, ended after the second phrase, 704 ms after the last speech chunk |
| 6 s of quiet-room noise | `NoSpeech` at 5.02 s |
| Inference time | 0.14–0.23 ms per 32 ms chunk (desktop CPU) |

**On the phone: needs Dan to measure.** Record the results here.

| Measurement | How | Result |
| --- | --- | --- |
| Silero is the engine in use | Settings title ×7: the developer section says "Silero VAD" (and log records say `"engine": "silero"`) | pending |
| Natural pauses don't end the turn | Wake word, then "turn on the… lounge lights" with a real hesitation, 10 tries | pending |
| End of speech within ~0.8 s | `latency_ms` in `endpoint_events.jsonl` over those turns (it should sit near 700–770) | pending |
| Silent wake turn | Say the wake word and nothing else: soft chime after 5 s, no error, listening again | pending |
| First word not clipped | "Hey Jarvis what time is it", without a pause: the transcript starts with "what" | pending |
| Inference time on the phone | `inference_us` in the log | pending |

If turns end mid-sentence, raise "Silence that ends a turn" (or lower the threshold). If they hang on after speech in a noisy room, raise the threshold.

## Consequences

- Wake word turns end about 300 ms sooner than with the energy endpointer (700 ms of silence instead of 1 s), and background noise that isn't speech no longer keeps a turn open or starts one.
- The APK is 2.3 MB bigger.
- `EndOfSpeechDetector` returns `Endpoint`, so STU-72 (follow-up and barge-in) can reuse the detector and the state machine for "is the user talking over the reply".
- STU-96 (speaker verification) can hang off the same per-turn audio path.
- Follow-up STU-128: keep the mic open through the wake chime, if first words turn out to be clipped (see "Pre-roll").
