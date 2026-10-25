package com.alfred.app.data.reliability

import com.alfred.app.data.voice.LinkState
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first

/**
 * Keeps [ServiceHealth] and the reliability log in step (ADR 0025). Everything that notices the
 * service starting, stopping, dying or reconnecting goes through here.
 */
class ReliabilityRecorder(
    private val health: HealthRepository,
    private val log: ReliabilityLog,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    /** The service is up and listening. Counts a restart if it last died rather than stopped. */
    suspend fun serviceStarted(reason: StartReason) {
        val now = clock()
        val gap = health.health.first().downFor(now)
        health.update { it.started(now, reason) }
        log.record(ReliabilityEvent(ReliabilityEvent.Kind.Started, now, reason.wire, gap))
    }

    suspend fun heartbeat() {
        val now = clock()
        health.update { it.heartbeat(now) }
    }

    /** Turned off on purpose (the Settings toggle); not a death. Does nothing if already stopped. */
    suspend fun serviceStopped() {
        if (health.health.first().cleanStop) return
        val now = clock()
        health.update { it.stoppedCleanly(now) }
        log.record(ReliabilityEvent(ReliabilityEvent.Kind.Stopped, now))
    }

    /** [by] (`watchdog`, `boot`) found the service not running while the wake word is on. */
    suspend fun down(by: String) {
        val now = clock()
        val gap = health.health.first().downFor(now)
        log.record(ReliabilityEvent(ReliabilityEvent.Kind.Down, now, by, gap))
    }

    /** Android wouldn't let the service record from the background; Dan has to tap to resume. */
    suspend fun restartBlocked(reason: String) {
        log.record(ReliabilityEvent(ReliabilityEvent.Kind.RestartBlocked, clock(), reason))
    }

    suspend fun reconnected() {
        health.update { it.reconnected() }
        log.record(ReliabilityEvent(ReliabilityEvent.Kind.Reconnect, clock()))
    }

    /**
     * Counts a reconnect each time [states] reaches Connected again after the socket dropped
     * (Waiting, or Connecting after a Connected). A deliberate stop (Offline: the app left the
     * screen with the wake word off, or the wake word was turned off) and a settings problem
     * start the count over, so coming back from those isn't one. Runs until cancelled.
     */
    suspend fun countReconnects(states: Flow<LinkState>) {
        var wasConnected = false
        var connected = false
        states.collect { state ->
            val isConnected = state is LinkState.Connected
            when {
                isConnected && !connected -> {
                    if (wasConnected) reconnected()
                    wasConnected = true
                }
                state is LinkState.Offline || state is LinkState.NotConfigured || state is LinkState.Rejected ->
                    wasConnected = false
                else -> Unit
            }
            connected = isConnected
        }
    }
}
