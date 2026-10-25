package com.alfred.app.data.reliability

import com.alfred.app.data.voice.DeviceHealth

/** Why the wake word service started (ADR 0025). */
enum class StartReason(val wire: String) {
    /** The app was opened with the wake word on. */
    App("app"),

    /** The phone finished booting. */
    Boot("boot"),

    /** The app was updated (Android stops its services when it does). */
    PackageReplaced("package_replaced"),

    /** The 15-minute watchdog found it not running. */
    Watchdog("watchdog"),

    /** Dan tapped "Tap to resume" on the stopped-listening notification. */
    Resume("resume"),

    /** Android restarted it after killing the process (`START_STICKY`). */
    Sticky("sticky"),
    ;

    companion object {
        fun fromWire(wire: String?): StartReason? = entries.firstOrNull { it.wire == wire }
    }
}

/**
 * How the wake word service has been doing, kept across process deaths (ADR 0025).
 *
 * The service writes [lastAliveAt] every minute while it runs and sets [cleanStop] only when it
 * is turned off on purpose. So a start that finds `cleanStop == false` means the process died
 * (or the phone was switched off) while listening: that's a restart, and the gap is
 * `now - lastAliveAt`, at most a minute more than the real time without the service.
 */
data class ServiceHealth(
    /** When the service last started; stale once it has died, so check it's running. */
    val listeningSince: Long? = null,
    val lastAliveAt: Long? = null,
    val cleanStop: Boolean = true,
    val restartCount: Int = 0,
    val lastRestartAt: Long? = null,
    val lastRestartReason: StartReason? = null,
    val lastGapMillis: Long? = null,
    val reconnectCount: Int = 0,
) {
    /** How long it has been down unexpectedly, if it has. */
    fun downFor(now: Long): Long? = if (!cleanStop && lastAliveAt != null) (now - lastAliveAt).coerceAtLeast(0) else null

    fun started(now: Long, reason: StartReason): ServiceHealth {
        val gap = downFor(now)
        val running = copy(listeningSince = now, lastAliveAt = now, cleanStop = false)
        return if (gap == null) {
            running
        } else {
            running.copy(
                restartCount = restartCount + 1,
                lastRestartAt = now,
                lastRestartReason = reason,
                lastGapMillis = gap,
            )
        }
    }

    fun heartbeat(now: Long): ServiceHealth = copy(lastAliveAt = now)

    fun stoppedCleanly(now: Long): ServiceHealth = copy(listeningSince = null, lastAliveAt = now, cleanStop = true)

    fun reconnected(): ServiceHealth = copy(reconnectCount = reconnectCount + 1)

    /**
     * What `hello.health` reports to the server. [running]: the service is up now; after a death
     * [listeningSince] is the dead service's start until the next start replaces it.
     */
    fun toDeviceHealth(running: Boolean): DeviceHealth = DeviceHealth(
        listeningSinceMillis = listeningSince.takeIf { running && !cleanStop },
        restarts = restartCount,
        reconnects = reconnectCount,
        lastRestartAtMillis = lastRestartAt,
        lastGapMillis = lastGapMillis,
    )
}

enum class WatchdogAction { Nothing, Restart }

/** What the watchdog does when it wakes up. */
fun watchdogAction(enabled: Boolean, running: Boolean, hasMic: Boolean): WatchdogAction =
    if (enabled && hasMic && !running) WatchdogAction.Restart else WatchdogAction.Nothing
