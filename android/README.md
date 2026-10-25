# Alfred Android client

Thin Kotlin + Jetpack Compose client for the Alfred home server. Today it has navigation, settings, a connection check, push-to-talk voice over the device WebSocket and an always-on wake word; phone actions come in later tickets.

## Stack

Single activity, Compose navigation and Material 3, ViewModels with coroutines/Flow, Koin for DI, OkHttp for HTTP (and WebSocket later), Preferences DataStore for settings. minSdk 29, target/compile SDK 37, JDK 21. Why these: `docs/decisions/0004-android-client-stack.md`.

| Path (under `app/src/main/java/com/alfred/app/`) | What |
| --- | --- |
| `AlfredApplication.kt`, `MainActivity.kt` | Koin start-up, the single activity |
| `di/AppModule.kt` | Koin module: OkHttp, settings, server client, ViewModels |
| `data/settings/` | `AppSettings` (server URL, API token) in DataStore |
| `data/server/` | `ServerClient`: checks `/health` then `/me` and returns a `ConnectionStatus`; pairs the device (`POST /devices/pair`) and answers confirmations |
| `data/device/` | `DeviceKeys` (EC P-256 key in the Android Keystore) and `ApprovalSigner`, which signs `app_ui` approvals (server ADR 0016) |
| `data/voice/` | Voice over `/ws/device` (`docs/protocol.md`, design in `docs/decisions/0017-android-voice-client.md`): `Protocol.kt` messages and audio frames; `DeviceConnection` (OkHttp WebSocket, `hello`, ping, reconnect with backoff); `DeviceIdRepository` (persistent `device_id`); `AudioCapture` (mic, 16 kHz PCM16, 20 ms frames); `AudioPlayer` (streaming `AudioTrack`, jitter buffer, ducking, Bluetooth); `VoiceController` (turn state machine and confirmations) |
| `data/wakeword/`, `service/WakeWordService.kt` | Wake word (`docs/decisions/0019-wake-word.md`): openWakeWord models on ONNX Runtime (`WakeWordModels`, `WakeWordDetector`), the listen/wake/turn loop (`WakeWordController`), its settings, phone signals that pause it, the wake event log, and the `microphone` foreground service |
| `ui/` | `AlfredApp` (menu drawer from Home), Home (status line, orb, greeting, push-to-talk, confirmation sheet; `HomeViewModel` + `VoiceViewModel`, ADR 0022), Settings, Voice Protocol (placeholder), Connectors and Activity screens |
| `ui/theme/` | Design system tokens (`docs/decisions/0018-android-design-system.md`): dark-only `AlfredTheme` with `AlfredTheme.colors` / `.typography` / `.spacing` (also mapped onto Material 3), `ChamferShape`, `OctagonShape`, `HexagonPillShape`. The only place colour literals may appear (`ThemeTest` enforces it) |
| `ui/components/` | Shared components: `GridBackground`, `ChamferIconButton`, `OctagonActionButton`, `PrimaryButton`, `HexagonButton`, `StatusPill`, `StatTile`, `SelectableCard`; all previewed in `DesignSystemPreviews.kt` |
| `ui/orb/` | `AlfredOrb` radar orb (`docs/decisions/0020-radar-orb.md`): `OrbState` (idle, listening, thinking, speaking), `OrbSize` (`Large` for Home, `Small` for Transmitting), mic/playback `level`, previews in `OrbPreviews.kt`; `OrbMotion` is its unit-tested animation model |
| `src/debug/` | Debug-build-only screens: `OrbDebugActivity` (the orb driven by fake speech levels) |

Fonts (Rajdhani, JetBrains Mono, Titillium Web) are bundled in `app/src/main/res/font/` under the SIL Open Font License; the licence texts are in `licenses/fonts/`.

## Build and test

Needs the Android SDK (`ANDROID_HOME`, or `sdk.dir` in a git-ignored `local.properties`) and JDK 21. Android Studio's bundled JBR works:

```bash
export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"   # Windows / Git Bash example
./gradlew lint testDebugUnitTest assembleDebug                    # what CI runs
./gradlew installDebug                                            # install on a connected phone (adb)
./gradlew testDebugUnitTest -Proborazzi.test.verify=true          # compare screenshot tests with their goldens (CI does)
./gradlew recordRoborazziDebug                                    # re-record goldens in app/src/test/screenshots/ after a deliberate UI change
```

The orb's debug screen (debug builds only) drives every orb state with fake speech levels:

```bash
adb shell am start -n com.alfred.app/com.alfred.app.debug.OrbDebugActivity
```

The APK lands in `app/build/outputs/apk/debug/app-debug.apk` (arm64 phones and the x86_64 emulator only: ONNX Runtime's native code is ~35 MB per ABI). CI also uploads it as the `alfred-debug-apk` artifact.

`OnnxWakeWordModelsTest` runs the real wake word models on desktop ONNX Runtime. On Windows it's skipped under Android Studio's JBR, whose old `msvcp140.dll` crashes ONNX Runtime; to run it, make the test JVM another JDK with an init script containing `allprojects { tasks.withType(Test).configureEach { executable = "C:/Program Files/Java/jdk-25/bin/java.exe" } }` and pass `--init-script <file>`. CI runs it on Linux.

The default server URL baked into the app is `alfred.serverUrl` in `gradle.properties` (`http://glados:8000`, the server's Tailscale MagicDNS name). Override it with `-Palfred.serverUrl=http://…` or in `~/.gradle/gradle.properties`. It can also be changed in the app's Settings.

## Connecting to the server

1. Set `ALFRED_API_TOKEN` on the server (see `infra/.env.example`) and make it listen on the tailnet: `ALFRED_HOST=0.0.0.0` (or the machine's Tailscale IP).
2. On the phone, turn Tailscale on, open Alfred → Settings, check the server URL, paste the same token and Save.
3. Home's status line should read **SECURE LINE · ENCRYPTED** (blue square). Other states:
   - **RECONNECTING…** / **CONNECTING…** (amber): the voice socket is (re)connecting.
   - **UNAUTHORISED** (red): wrong token, or a saved token with characters that can't go in an HTTP header (Settings refuses to save such tokens).
   - **LINE DOWN** (red): Tailscale is off, the server is down, or it answered with an error (503: no token configured on the server).
   - **NOT SET UP** (red): no server URL or token yet.

   The crosshair button (top right) opens the details in words, with **Check now**. Settings → Home screen sets the name in the greeting and the place before the clock.

## Pairing for approvals

Irreversible actions (unlocking a door, say) can only be approved from a paired app: the shared API token isn't enough. Pairing registers a key that lives in the phone's Keystore and never leaves it.

1. Save the server URL and token in Settings first (pairing uses the saved values).
2. On the server, in `server/`: `uv run python -m alfred_server.devices pair --name pixel`. It prints a code like `ABCD-EFGH`, valid for 10 minutes and usable once.
3. In the app, open Settings → Approvals, enter the code and tap **Pair**. It should then say **Paired as pixel**.
4. To check or revoke a pairing on the server, run `uv run python -m alfred_server.devices list`, then `revoke <id>`.

## Talking to Alfred

Home's mic button works once the voice socket is connected (the server needs `ALFRED_STT_ENABLED=true` and `ALFRED_TTS_ENABLED=true`; otherwise the crosshair's sheet says voice is off). While it isn't, the button is dimmed; tapping it says so and retries the connection.

- **Hold** the button, speak, let go. Or **tap** to start and tap again to send.
- Tap while Alfred is thinking or speaking to stop him.
- The first press asks for the microphone permission. If it was denied, Home offers a link to the app's system settings.
- The crosshair's sheet shows the last turn and how it ran: specialist, model, local or cloud, tools, tokens, cost and time to first audio.
- When Alfred needs an OK, a sheet shows what he wants to do. Ordinary actions can be approved there or by saying yes or no. Irreversible ones (unlocking a door) can only be approved on the sheet, and only on a paired phone (see below).
- The voice socket is open while the app is on screen and reconnects by itself after a network drop (the status line shows RECONNECTING…; the crosshair's sheet says *reconnecting in N s*). Tapping the mic while it's down retries straight away.

## Wake word

Settings → Wake word turns on hands-free listening (it asks for the microphone, and on Android 13+ the notification permission).

- Say the wake word ("Hey Jarvis" by default; Alexa and Hey Mycroft are the other stock models), then your request. A beep (if Chime is on) and a light buzz mean Alfred heard you. The turn ends after about a second of silence.
- Alfred keeps listening with the screen off and the app in the background, shown by an ongoing notification with **Pause** / **Resume**. Nothing leaves the phone until the wake word is heard: detection runs on the phone.
- If it woke by mistake, tap **That wasn't me** on the notification (offered for 30 s). It stops the turn and logs a false accept. Settings shows the counts; the full log is `adb shell run-as com.alfred.app cat files/wake_events.jsonl`.
- End of speech is Silero VAD ([ADR 0024](../docs/decisions/0024-end-of-speech-vad.md)): a turn ends after 0.7 s of quiet, and a wake turn where nobody speaks for 5 s is cancelled with a soft chime. Tap the **Settings** title 7 times for the developer section (thresholds, energy fallback switch, recent turns). Each turn's end is logged to `adb shell run-as com.alfred.app cat files/endpoint_events.jsonl` (`latency_ms` is how long after the last speech the turn ended) and logcat tag `Alfred/VAD`.
- **Sensitivity**: higher wakes more easily, and more often by mistake.
- It pauses during calls, while another app is recording, and (by default) while Do Not Disturb is on.
- After a reboot, open the app once to start listening again (boot start comes with STU-71).
