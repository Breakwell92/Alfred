package com.alfred.app.data.reliability

import com.alfred.app.data.voice.DeviceHealth
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class ServiceHealthTest {

    @Test
    fun `the first start isn't a restart`() {
        val health = ServiceHealth().started(1_000, StartReason.App)

        assertEquals(1_000L, health.listeningSince)
        assertEquals(1_000L, health.lastAliveAt)
        assertFalse(health.cleanStop)
        assertEquals(0, health.restartCount)
        assertNull(health.lastGapMillis)
    }

    @Test
    fun `a start after a clean stop isn't a restart`() {
        val health = ServiceHealth().started(1_000, StartReason.App).stoppedCleanly(5_000).started(9_000, StartReason.App)

        assertEquals(0, health.restartCount)
        assertEquals(9_000L, health.listeningSince)
    }

    @Test
    fun `a start after a death is a restart, with the gap since the last heartbeat`() {
        val health = ServiceHealth()
            .started(0, StartReason.App)
            .heartbeat(60_000)
            .heartbeat(120_000)
            // The process dies here; nothing is written.
            .started(840_000, StartReason.Watchdog)

        assertEquals(1, health.restartCount)
        assertEquals(840_000L, health.lastRestartAt)
        assertEquals(StartReason.Watchdog, health.lastRestartReason)
        assertEquals(720_000L, health.lastGapMillis)
        assertEquals(840_000L, health.listeningSince)
    }

    @Test
    fun `down for is only set after a death`() {
        assertNull(ServiceHealth().downFor(10_000))
        assertNull(ServiceHealth().started(0, StartReason.App).stoppedCleanly(1_000).downFor(10_000))
        assertEquals(9_000L, ServiceHealth().started(1_000, StartReason.App).downFor(10_000))
    }

    @Test
    fun `reconnects are counted`() {
        assertEquals(2, ServiceHealth().reconnected().reconnected().reconnectCount)
    }

    @Test
    fun `reports listening since only while it should be running`() {
        val running = ServiceHealth().started(2_000, StartReason.Boot).reconnected()
        assertEquals(DeviceHealth(2_000, restarts = 0, reconnects = 1), running.toDeviceHealth())
        assertNull(running.stoppedCleanly(3_000).toDeviceHealth().listeningSinceMillis)
    }

    @Test
    fun `the watchdog restarts only an enabled, permitted, stopped service`() {
        assertEquals(WatchdogAction.Restart, watchdogAction(enabled = true, running = false, hasMic = true))
        assertEquals(WatchdogAction.Nothing, watchdogAction(enabled = true, running = true, hasMic = true))
        assertEquals(WatchdogAction.Nothing, watchdogAction(enabled = false, running = false, hasMic = true))
        assertEquals(WatchdogAction.Nothing, watchdogAction(enabled = true, running = false, hasMic = false))
    }

    @Test
    fun `start reasons round-trip their wire names`() {
        StartReason.entries.forEach { assertEquals(it, StartReason.fromWire(it.wire)) }
        assertNull(StartReason.fromWire("nonsense"))
        assertNull(StartReason.fromWire(null))
    }
}
