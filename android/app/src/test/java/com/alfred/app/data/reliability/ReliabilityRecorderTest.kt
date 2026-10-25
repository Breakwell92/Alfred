package com.alfred.app.data.reliability

import com.alfred.app.data.reliability.ReliabilityEvent.Kind
import com.alfred.app.data.voice.LinkState
import com.alfred.app.testing.FakeDeviceConnection
import com.alfred.app.testing.FakeHealthRepository
import com.alfred.app.testing.FakeReliabilityLog
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReliabilityRecorderTest {

    private val health = FakeHealthRepository()
    private val log = FakeReliabilityLog()
    private var now = 0L
    private val recorder = ReliabilityRecorder(health, log) { now }

    @Test
    fun `logs a start, and a restart with its gap after a death`() = runTest {
        recorder.serviceStarted(StartReason.App)
        now = 60_000
        recorder.heartbeat()
        now = 360_000
        recorder.serviceStarted(StartReason.Sticky)

        assertEquals(
            listOf(
                ReliabilityEvent(Kind.Started, 0, "app", null),
                ReliabilityEvent(Kind.Started, 360_000, "sticky", 300_000),
            ),
            log.events,
        )
        assertEquals(1, health.health.value.restartCount)
    }

    @Test
    fun `a stop is recorded once, and only after a start`() = runTest {
        recorder.serviceStopped()
        assertTrue(log.events.isEmpty())

        recorder.serviceStarted(StartReason.App)
        now = 5_000
        recorder.serviceStopped()
        recorder.serviceStopped()

        assertEquals(listOf(Kind.Started, Kind.Stopped), log.events.map { it.kind })
        assertTrue(health.health.value.cleanStop)
    }

    @Test
    fun `down and blocked restarts are logged with who noticed`() = runTest {
        recorder.serviceStarted(StartReason.App)
        now = 900_000
        recorder.down("watchdog")
        recorder.restartBlocked("watchdog")

        assertEquals(
            listOf(
                ReliabilityEvent(Kind.Down, 900_000, "watchdog", 900_000),
                ReliabilityEvent(Kind.RestartBlocked, 900_000, "watchdog"),
            ),
            log.events.drop(1),
        )
    }

    @Test
    fun `counts a reconnect each time it's connected again, not the first time`() = runTest {
        val states = MutableStateFlow<LinkState>(LinkState.Connecting)
        val job = launchCounting(states)

        states.value = LinkState.Connected("s", voice = true)
        states.value = LinkState.Connected("s2", voice = true) // still connected: not a reconnect
        states.value = LinkState.Waiting("gone", 1_000)
        states.value = LinkState.Connecting
        states.value = LinkState.Connected("s", voice = true)
        states.value = LinkState.Offline
        states.value = LinkState.Connected("s", voice = true)
        job.cancel()

        assertEquals(2, health.health.value.reconnectCount)
        assertEquals(2, log.events.count { it.kind == Kind.Reconnect })
    }

    @Test
    fun `the monitor forwards network callbacks and counts reconnects`() = runTest {
        val connection = FakeDeviceConnection(LinkState.Connecting)
        var network: (() -> Unit)? = null
        val monitor = ReliabilityMonitor(
            connection,
            recorder,
            { network = it },
            TestScope(UnconfinedTestDispatcher(testScheduler)),
        )

        monitor.start()
        monitor.start()
        network!!()
        connection.state.value = LinkState.Connected("s", voice = true)
        connection.state.value = LinkState.Waiting("gone", 1_000)
        connection.state.value = LinkState.Connected("s", voice = true)

        assertEquals(1, connection.networkSignals)
        assertEquals(1, health.health.value.reconnectCount)
    }

    private fun TestScope.launchCounting(states: MutableStateFlow<LinkState>) =
        launch(UnconfinedTestDispatcher(testScheduler)) { recorder.countReconnects(states) }
}
