# 0019: Wake word on the phone (openWakeWord)

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-68

## Context

Alfred should start listening when Dan says a wake word, with the screen off and the app in the background, and stream nothing until then. Detection has to run on the phone, all day, cheaply. Porcupine is out (Picovoice's free keys stopped working after 30 June 2026). openWakeWord is open source and has stock models, and later we can train our own "Hey Alfred" (STU-70). The push-to-talk pipeline from STU-65 ([ADR 0017](0017-android-voice-client.md)) already does the turn itself.

## Decision

**Engine: openWakeWord's models on ONNX Runtime, run by our own code.** We use `com.microsoft.onnxruntime:onnxruntime-android` and a small Kotlin port of openWakeWord's streaming pipeline (`data/wakeword/WakeWordDetector.kt`). The ticket suggested `xyz.rementia:openwakeword` (Re-MENTIA/openwakeword-android-kt). We looked at it and didn't use it, for three reasons:
- It isn't published to any Maven repository; you build it with `publishToMavenLocal`.
- It records the mic itself, so it couldn't share `AudioCapture` with the turn.
- It creates every ONNX session again (reading the model bytes) for every 80 ms frame, which would cost a lot of battery.

**Pipeline** (as openWakeWord's `AudioFeatures`). The input is 16 kHz PCM16, taken as raw int16 values (not scaled), and processed in 80 ms (1280-sample) chunks. For each chunk:
- `melspectrogram.onnx` runs over the chunk plus 480 samples of context, and the output is rescaled to x/10 + 2.
- `embedding_model.onnx` runs over the last 76 mel frames, giving a 96-value embedding.
- The wake word classifier scores the last 16 embeddings.
- The mel buffer starts as ones and the embedding buffer starts from embeddings of random noise. The first 5 scores after a start or reset are ignored.
- A wake is a score of at least the threshold, with at least 2 s between wakes.

The three sessions are created once per model and run single-threaded. Models are bundled in `assets/wakeword/` from openWakeWord release v0.5.1: mel, embedding, `hey_jarvis` (the default), `alexa` and `hey_mycroft`, about 5.4 MB in all.

**Sensitivity.** The Settings slider maps 0 to 1 onto a threshold from 0.8 to 0.2. The default of 0.5 gives openWakeWord's default threshold of 0.5.

**Service.** `WakeWordService` is a foreground service of type `microphone`. Its low-importance notification shows the state and has Pause / Resume, plus "That wasn't me" for 30 s after a wake. While it runs it holds the device socket (`LinkHolds`), so leaving the app no longer closes the socket or cancels a turn. `MainActivity` starts and stops it to follow the Settings toggle. Android only allows a microphone service to start while the app is in the foreground.

**Loop** (`WakeWordController`):
1. While enabled, not paused and with no turn running, stream `AudioCapture` into the detector.
2. On a wake, end that recording, so the mic is released before anything else.
3. Buzz lightly, and play a 150 ms beep if the chime is on. The beep finishes before the mic reopens, so it isn't sent as part of the request.
4. Start a hands-free turn: `VoiceController.startListening(endOfSpeech)`.
5. Listen again once the voice phase is back to Idle. Push-to-talk turns also stop wake word listening while they run.

**Pausing.** Listening pauses (and the mic is released) when:
- the user pauses it from the notification;
- there's a phone or VoIP call, or the phone is ringing (audio mode);
- another app is recording (`AudioRecordingCallback`, ignoring our own audio sessions);
- Do Not Disturb (which covers bedtime mode) is on, if "Pause while Do Not Disturb is on" is set, which is the default.

**End of speech (temporary; replaced by Silero VAD in [ADR 0024](0024-end-of-speech-vad.md)).** A wake word turn has no button release. Until Silero VAD (STU-69), `EnergyEndOfSpeech` (`data/voice/EndOfSpeech.kt`) ended the turn. It is now the fallback when the model can't load:
- after 1 s of quiet once speech has started;
- after 5 s if nobody speaks;
- at 15 s in any case.

A 20 ms frame counts as speech when its RMS is 3× the quietest frame of the last 3 s and at least 300. Blank frames don't count towards that floor. Speech has only "started" after 200 ms of it, and nothing in the first 300 ms after the mic opens counts at all. Without those two rules, the first phone test ended every turn after about 1.5 s: the chime's echo and the recorder starting up looked like speech, then the wait for Dan to begin looked like the pause at the end. The server got about 1.5 s of near-silence, and Whisper heard "Thank you." STU-69 replaces it behind the same `EndOfSpeechDetector` interface.

**Tuning data.** Every wake and every "That wasn't me" is appended to `files/wake_events.jsonl` (`{kind, at, model, score}`). Read it with `adb shell run-as com.alfred.app cat files/wake_events.jsonl`. Settings shows the counts.

**Native libraries.** ONNX Runtime is about 35 MB of native code per ABI, so the APK keeps `arm64-v8a` and `x86_64` (for the emulator) only. On any other ABI, the listener reports that it can't run instead of crashing.

**Not done here:** starting on boot. Android 15 doesn't let a `BOOT_COMPLETED` receiver start a `microphone` foreground service. Boot start, battery optimisation and OEM task killers are STU-71, see [ADR 0025](0025-background-reliability.md).

## Results

Checked in JVM tests (`OnnxWakeWordModelsTest`, desktop ONNX Runtime, the real bundled models). These tests run in CI. Locally on Windows they're skipped on Android Studio's JBR, because its bundled `msvcp140.dll` (14.29) is too old for ONNX Runtime 1.31; run them on another JDK, see `android/README.md`.

| Clip | Max score ("hey jarvis" model) |
| --- | --- |
| Synthetic "Hey Jarvis." (Windows SAPI voice) | 0.998 |
| "Set a timer for ten minutes" (`server/tests/fixtures/audio/timer.wav`) | 0.078 |
| 5 s of quiet-room noise | 0.078 |

**On the phone: needs Dan to measure.** Record the results here.

| Measurement | How | Result |
| --- | --- | --- |
| Screen off, app in background: wake word starts a turn and Alfred replies | Enable it in Settings, lock the phone, say "Hey Jarvis, what time is it?" | pending |
| Idle CPU over 1 h | Phone unplugged, screen off, service listening: `adb shell top -b -n 1 -p $(adb shell pidof com.alfred.app)` every 10 min (or `adb shell dumpsys cpuinfo \| grep com.alfred.app`) | pending |
| Battery over 1 h | `adb shell dumpsys batterystats --reset`, unplug, wait 1 h with screen off, then `adb shell dumpsys batterystats com.alfred.app` (estimated power use) and the overall % drop | pending |
| Detection at 2–3 m, quiet room | 20 tries at 2 m and 20 at 3 m, normal voice; count wakes | pending |
| False accepts with the TV on | 1 h or more of TV with nobody speaking to it; count wakes (each one is in `wake_events.jsonl`) | pending (target < ~1/hour) |

If false accepts are too high, lower the sensitivity. If wakes are missed at 3 m, raise it. Then record the setting used.

## Consequences

- STU-69 (Silero VAD, [ADR 0024](0024-end-of-speech-vad.md)) replaced `EnergyEndOfSpeech`, reusing ONNX Runtime. A wake turn where nobody speaks is now cancelled quietly with a soft chime.
- STU-70 (custom "Hey Alfred") adds a model file and a `StockWakeWord`-like entry. The detector doesn't change.
- STU-71 ([ADR 0025](0025-background-reliability.md)) added boot start, a watchdog and battery-optimisation guidance. Where Android refuses a background start of the mic service (boot on Android 14+, `START_STICKY` restarts, the watchdog), the service shows a "Tap to resume" notification instead of stopping silently.
- STU-72 (follow-up / barge-in) can use the same `EndOfSpeechDetector` hook.
- The app is about 75 MB bigger (debug) because of ONNX Runtime's native libraries.
- The stock wake word models are CC BY-NC-SA 4.0 (non-commercial), which is fine for this personal app (`android/licenses/openwakeword/`).
