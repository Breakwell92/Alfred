package com.alfred.app.data.wakeword

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.testing.FakeAudioCapture
import com.alfred.app.testing.FakeAudioPlayer
import com.alfred.app.testing.FakeDeviceConnection
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeServerClient
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.FakeWakeEventLog
import com.alfred.app.testing.FakeWakeFeedback
import com.alfred.app.testing.FakeWakeSignals
import com.alfred.app.testing.FakeWakeWordModels
import com.alfred.app.testing.FakeWakeWordSettingsRepository
import com.alfred.app.testing.pcm
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * Stopping while inference is running on a real worker thread: the ONNX sessions must not be
 * closed until that call has returned (closing them mid-run is a native use-after-free).
 */
class WakeWordControllerStopTest {

    // Two threads, like Dispatchers.Default: a close could otherwise only queue behind the scoring.
    private val worker = Executors.newFixedThreadPool(2).asCoroutineDispatcher()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
    private val capture = FakeAudioCapture()
    private val models = FakeWakeWordModels()
    private var loads = 0

    @After
    fun tearDown() {
        scope.cancel()
        worker.close()
    }

    private fun controller(): WakeWordController {
        val voice = VoiceController(
            FakeDeviceConnection(),
            capture,
            FakeAudioPlayer(),
            FakeServerClient(),
            FakeSettingsRepository(AppSettings("http://glados:8000", "token")),
            ApprovalSigner(FakeDeviceKeys()),
            scope,
        )
        return WakeWordController(
            FakeWakeWordSettingsRepository(), voice, capture, FakeWakeSignals(), FakeWakeFeedback(),
            FakeWakeEventLog(),
            loadModels = {
                loads++
                models
            },
            scope = scope,
            workDispatcher = worker,
        )
    }

    private suspend fun waitFor(what: String, condition: () -> Boolean) = withTimeout(5_000) {
        while (!condition()) delay(5)
    }.also { check(condition()) { what } }

    @Test
    fun `stop waits for the chunk being scored before closing the models`() = runBlocking {
        val wake = controller()
        wake.start()
        waitFor("listening") { capture.open }

        val hold = CountDownLatch(1)
        models.holdScore = hold
        capture.frames.trySend(pcm(1))
        waitFor("scoring") { "score-start" in models.calls }

        wake.stop()
        // Give a premature close every chance to happen.
        delay(100)
        assertFalse(models.closed)

        hold.countDown()
        waitFor("closed") { models.closed }
        val calls = models.calls.toList()
        assertEquals("close", calls.last())
        assertEquals(calls.indexOf("score-end") + 1, calls.indexOf("close"))
    }

    @Test
    fun `starting again right after stop waits for the old loop and loads fresh models`() = runBlocking {
        val wake = controller()
        wake.start()
        waitFor("listening") { capture.open }

        val hold = CountDownLatch(1)
        models.holdScore = hold
        capture.frames.trySend(pcm(1))
        waitFor("scoring") { "score-start" in models.calls }

        wake.stop()
        wake.start()
        delay(100)
        assertEquals(1, loads) // the new loop hasn't touched the models the old one is using

        hold.countDown()
        models.holdScore = null
        waitFor("closed") { models.closed }
        waitFor("reloaded") { loads == 2 }
        assertEquals(1, models.calls.count { it == "close" })
    }
}
