# Alfred Android client

Thin Kotlin + Jetpack Compose client for the Alfred home server. Today it has navigation, settings and a connection check; wake word, audio and phone actions come in later tickets.

## Stack

Single activity, Compose navigation and Material 3, ViewModels with coroutines/Flow, Koin for DI, OkHttp for HTTP (and WebSocket later), Preferences DataStore for settings. minSdk 29, target/compile SDK 37, JDK 21. Why these: `docs/decisions/0004-android-client-stack.md`.

| Path (under `app/src/main/java/com/alfred/app/`) | What |
| --- | --- |
| `AlfredApplication.kt`, `MainActivity.kt` | Koin start-up, the single activity |
| `di/AppModule.kt` | Koin module: OkHttp, settings, server client, ViewModels |
| `data/settings/` | `AppSettings` (server URL, API token) in DataStore |
| `data/server/` | `ServerClient`: checks `/health` then `/me` and returns a `ConnectionStatus`; pairs the device (`POST /devices/pair`) and answers confirmations |
| `data/device/` | `DeviceKeys` (EC P-256 key in the Android Keystore) and `ApprovalSigner`, which signs `app_ui` approvals (server ADR 0016) |
| `ui/` | `AlfredApp` (bottom nav), Home, Settings, Connectors and Activity screens, theme |

## Build and test

Needs the Android SDK (`ANDROID_HOME`, or `sdk.dir` in a git-ignored `local.properties`) and JDK 21. Android Studio's bundled JBR works:

```bash
export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"   # Windows / Git Bash example
./gradlew lint testDebugUnitTest assembleDebug                    # what CI runs
./gradlew installDebug                                            # install on a connected phone (adb)
```

The APK lands in `app/build/outputs/apk/debug/app-debug.apk`. CI also uploads it as the `alfred-debug-apk` artifact.

The default server URL baked into the app is `alfred.serverUrl` in `gradle.properties` (`http://glados:8000`, the server's Tailscale MagicDNS name). Override it with `-Palfred.serverUrl=http://…` or in `~/.gradle/gradle.properties`. It can also be changed in the app's Settings.

## Connecting to the server

1. Set `ALFRED_API_TOKEN` on the server (see `infra/.env.example`) and make it listen on the tailnet: `ALFRED_HOST=0.0.0.0` (or the machine's Tailscale IP).
2. On the phone, turn Tailscale on, open Alfred → Settings, check the server URL, paste the same token and Save.
3. Home should show **Connected**. Other states:
   - **Unauthorised**: wrong token.
   - **Unreachable**: Tailscale is off or the server is down.
   - **Server error** (503): the server has no token configured.
   - **Invalid token**: the saved token has characters that can't go in an HTTP header (Settings refuses to save such tokens).

## Pairing for approvals

Irreversible actions (unlocking a door, say) can only be approved from a paired app: the shared API token isn't enough. Pairing registers a key that lives in the phone's Keystore and never leaves it.

1. Save the server URL and token in Settings first (pairing uses the saved values).
2. On the server, in `server/`: `uv run python -m alfred_server.devices pair --name pixel`. It prints a code like `ABCD-EFGH`, valid for 10 minutes and usable once.
3. In the app, open Settings → Approvals, enter the code and tap **Pair**. It should then say **Paired as pixel**.
4. To check or revoke a pairing on the server, run `uv run python -m alfred_server.devices list`, then `revoke <id>`.
