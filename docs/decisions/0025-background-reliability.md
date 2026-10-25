# 0025: Keeping the wake word alive in the background

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-71

## Context

The wake word service ([ADR 0019](0019-wake-word.md)) is a `microphone` foreground service. It should run for days. Two things work against that:

- **Phone makers kill background apps.** Samsung, Xiaomi, OnePlus and others go further than stock Android (see [dontkillmyapp.com](https://dontkillmyapp.com)). Doze also delays timers and cuts the network.
- **Android won't let a microphone service start from the background.** The app targets SDK 37, and Android only grants microphone access to a foreground service when the app starts it while it's in use: from a visible activity, from a notification tap, and a few system cases. Starting it from `BOOT_COMPLETED` is explicitly blocked for `microphone` services (Android 14+). Starting it from a WorkManager job or through a `START_STICKY` restart after the process was killed counts as background, so either the start throws (`ForegroundServiceStartNotAllowedException`) or the service's `startForeground` does (`SecurityException`).

So the app can notice when the service is gone, but on current Android it can't always bring it back without Dan.

## Decision

**One way back for every background path: try to start the service, and if Android refuses, ask Dan with a notification.** The notification says "Alfred stopped listening · Tap to resume". The tap opens `MainActivity`, a visible activity, which is allowed to start the service (with start reason `resume`). On phones and Android versions that do allow the start, the service just comes back. Code: `data/reliability/ServiceStarter.kt` (`AndroidServiceStarter`, `StoppedListeningNotification`).

The background paths are:
- **Boot and app update.** `service/BootReceiver` handles `BOOT_COMPLETED` and `MY_PACKAGE_REPLACED`, and does something only if the wake word is on and the mic is granted. It schedules the watchdog and tries to start the service. It doesn't handle `LOCKED_BOOT_COMPLETED`, because the settings DataStore can't be read before the first unlock.
- **The watchdog.** `data/reliability/Watchdog.kt` is a WorkManager periodic job every 15 minutes, which is WorkManager's minimum. It runs while the wake word is on: `MainActivity` schedules it when the toggle is on and cancels it when it's off. If the service should be running and isn't (`watchdogAction`), it logs `down` with the gap so far and tries to start the service.
- **Process killed.** `START_STICKY` stays. When Android restarts the service in the background, `startForeground` fails and the service shows the notification and stops itself, instead of failing silently as it did before.

The notification uses `setOnlyAlertOnce`, so the watchdog posting it again every 15 minutes doesn't buzz each time.

**Health: heartbeats and clean stops, so a restart knows its gap.** `ServiceHealth` is kept in the settings DataStore under `health_*` keys and works like this:
- While running, the service writes `lastAliveAt` every minute.
- The Settings toggle turning the service off marks `cleanStop`. `onDestroy` doesn't, because Android can stop it too.
- A start that finds `cleanStop == false` was a death: it counts as a restart, with gap = now − `lastAliveAt` (at most a minute more than the real gap) and the start reason (`boot`, `watchdog`, `resume`, `sticky`, `package_replaced`, `app`).
- Reconnects are counted from the socket's state: each Connected after an earlier Connected in the same process.

`ReliabilityRecorder` is the only thing that writes all this, and it also appends to `files/reliability_events.jsonl`, one JSON object per line: `{kind, at (epoch ms), reason?, gap_ms?}`. The kinds are:

| Kind | Meaning |
| --- | --- |
| `started` | The service is up and listening (with `gap_ms` if it had died) |
| `stopped` | Turned off in Settings |
| `down` | The watchdog or boot found it not running (`gap_ms` so far) |
| `restart_blocked` | Android refused a background start, so the notification was posted |
| `reconnect` | The socket came back after dropping |

**Shown in the app and sent to the server.**
- Settings → Wake word → "In the background" shows "Listening since 09:12", "Restarted 2× · last at 03:40 after a 12 min gap" and "Reconnected to the server 5×".
- The same numbers go to the server in `hello.health` ([protocol](../protocol.md)). The server writes them in the connect log line and keeps them on the device's connection.

**Battery exemption and the manufacturer guide.**
- Turning the wake word on (the onboarding step) also opens Android's "ignore battery optimisations" dialog if Alfred isn't exempt yet. This uses `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`. Google Play restricts that permission, but this is a personal app that's never on Play, so lint's `BatteryLife` warning is suppressed. If an OEM build has no such dialog, the battery optimisation list opens instead.
- Settings shows whether the exemption is granted, rechecked whenever Settings comes back on screen, with an **Allow** button when it isn't.
- Settings also links to the dontkillmyapp.com page for the phone's maker (`dontKillMyAppUrl(Build.MANUFACTURER)`, or the site's home page for makers without one).

**Reconnects tuned for Doze** (`DeviceConnection`):
- The backoff is now 1, 2, 4, 8, 16, 32, then 60 s (it used to stop growing at 15 s). With no network, frequent retries only wake the phone.
- `ReliabilityMonitor` registers a default-network callback, which Doze doesn't delay the way it delays timers. When a network becomes available, `networkAvailable()` skips the remaining wait and starts the backoff over.
- A refused token still waits for a settings change or Talk, whatever the network does.

## Results

**Needs Dan to verify on the phone.** Record the results here.

Read the log with `adb shell run-as com.alfred.app cat files/reliability_events.jsonl`. Settings → Wake word shows the same counts.

| Check | How | Result |
| --- | --- | --- |
| 48 h soak, normal use | Wake word on, battery exemption allowed, use the phone normally for 48 h. Say the wake word now and then. Afterwards, list the `started` events with `gap_ms` and every `down` / `restart_blocked`. | pending |
| Reboot | Reboot with the wake word on. Note whether the service started by itself or the "Tap to resume" notification appeared, and whether one tap brings it back. | pending |
| Swipe from recents | Swipe Alfred away in recents, wait 1 min, then say the wake word. If it stopped, note whether the notification appears (straight away, or at the next watchdog run ≤ 15 min). | pending |
| Force stop | Settings → Apps → Alfred → Force stop. Android then blocks receivers, jobs and restarts until the app is opened again. That's expected, and nothing can recover it. Opening the app starts listening again. | pending |
| Battery dialog | Turn the wake word off and on with the exemption not granted. The dialog appears; after allowing it, Settings shows "Battery: unrestricted". | pending |
| Server log | With the server running, reconnect the phone. The log shows `device … connected (app …; restarts N, reconnects M, last gap S s)`. | pending |

## Consequences

- On Android 14+ a reboot or a killed process usually means one tap to resume rather than nothing at all. Dan finds out from the notification instead of from a wake word that didn't work. Fully automatic recovery would need privileges a normal app can't have, such as being the phone's assistant app (`VoiceInteractionService`). That's a possible later ticket.
- The watchdog can take up to 15 minutes, plus whatever Doze adds, to notice.
- Gaps are measured to within a minute (the heartbeat interval). Gaps that end with a reboot include the time the phone was off.
- STU-94 (health dashboard) can read `DeviceConnection.health` on the server, or the phone's numbers from the app.
