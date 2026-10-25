package com.alfred.app.data.wakeword

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.ClientMessage
import com.alfred.app.data.voice.EndOfSpeechDetector
import com.alfred.app.data.voice.Endpoint
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
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
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class WakeWordControllerTest {

    private val connection = FakeDeviceConnection()
    private val capture = FakeAudioCapture()
    private val settings = FakeWakeWordSettingsRepository()
    private val signals = FakeWakeSignals()
    private val feedback = FakeWakeFeedback { capture.open }
    private val events = FakeWakeEventLog()
    private val models = FakeWakeWordModels(defaultScore = 0.9f)
    private val loaded = mutableListOf<StockWakeWord>()
    private var endOfSpeech: EndOfSpeechDetector = EndOfSpeechDetector { Endpoint.Continue }

    private lateinit var voice: VoiceController

    private fun TestScope.controller(): WakeWordController {
        val dispatcher = UnconfinedTestDispatcher(testScheduler)
        voice = VoiceController(
            connection = connection,
            capture = capture,
            player = FakeAudioPlayer(),
            serverClient = FakeServerClient(),
            settingsRepository = FakeSettingsRepository(AppSettings("http://glados:8000", "token")),
            signer = ApprovalSigner(FakeDeviceKeys()) { 1_700_000_000 },
            scope = backgroundScope,
            ioDispatcher = dispatcher,
        )
        return WakeWordController(
            settingsRepository = settings,
            voice = voice,
            capture = capture,
            signals = signals,
            feedback = feedback,
            events = events,
            loadModels = { model ->
                loaded += model
                models
            },
            scope = backgroundScope,
            workDispatcher = dispatcher,
            clock = { testScheduler.currentTime },
            monotonicClock = { testScheduler.currentTime },
            endOfSpeech = { endOfSpeech },
        )
    }

    private fun runWakeTest(block: suspend TestScope.() -> Unit) = runTest(UnconfinedTestDispatcher(), testBody = block)

    /** Enough audio to get past the warm-up and score above the threshold. */
    private fun sayWakeWord() {
        capture.frames.trySend(pcm(WakeWordDetector.WARMUP_CHUNKS + 1))
    }

    private val turnStarts get() = connection.sent.filterIsInstance<ClientMessage.TurnStart>()

    @Test
    fun `listens with the mic open while enabled`() = runWakeTest {
        val wake = controller()
        wake.start()
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
        assertTrue(capture.open)
        assertEquals(listOf(StockWakeWord.HeyJarvis), loaded)
    }

    @Test
    fun `wake word starts a hands-free turn after the mic is released`() = runWakeTest {
        val wake = controller()
        wake.start()
        sayWakeWord()

        assertEquals(listOf(true), feedback.wakes)
        // The detector's recording was closed before the chime and the turn's own recording.
        assertEquals(listOf(false), feedback.micOpenAtWake)
        assertEquals(1, turnStarts.size)
        assertEquals(VoicePhase.Listening, voice.state.value.phase)
        assertTrue(capture.open)
        assertEquals(WakeState.InTurn, wake.state.value)
        assertEquals(listOf(WakeEvent.Kind.Detection), events.events.map { it.kind })
        assertEquals(0.9f, wake.recentWake.value?.score)
    }

    @Test
    fun `nobody speaking cancels the turn quietly with the soft chime`() = runWakeTest {
        val wake = controller()
        var frames = 0
        endOfSpeech = EndOfSpeechDetector { if (++frames >= 2) Endpoint.NoSpeech else Endpoint.Continue }
        wake.start()
        sayWakeWord()

        capture.frames.trySend(ByteArray(640))
        capture.frames.trySend(ByteArray(640))
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertEquals(null, voice.state.value.error)
        assertTrue(connection.sent.any { it is ClientMessage.TurnCancel })
        assertFalse(connection.sent.any { it is ClientMessage.TurnEnd })
        assertEquals(listOf(true), feedback.noSpeech)
        // And it's listening for the wake word again.
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
    }

    @Test
    fun `the end-of-speech detector ends the turn`() = runWakeTest {
        val wake = controller()
        var frames = 0
        endOfSpeech = EndOfSpeechDetector { if (++frames >= 2) Endpoint.End else Endpoint.Continue }
        wake.start()
        sayWakeWord()

        capture.frames.trySend(ByteArray(640))
        assertEquals(VoicePhase.Listening, voice.state.value.phase)
        capture.frames.trySend(ByteArray(640))
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)
        assertTrue(connection.sent.any { it is ClientMessage.TurnEnd })
        assertFalse(capture.open)
    }

    @Test
    fun `listens again when the turn is over`() = runWakeTest {
        val wake = controller()
        wake.start()
        sayWakeWord()
        voice.cancel()

        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
        assertTrue(capture.open)
        // Fresh buffers: warming up again, so a leftover chunk can't wake it straight away.
        capture.frames.trySend(pcm(WakeWordDetector.WARMUP_CHUNKS))
        assertEquals(1, turnStarts.size)
        // Still within the cooldown of the first wake, which this test's clock hasn't moved past.
        advanceTimeBy(WakeWordDetector.DEFAULT_COOLDOWN_MILLIS)
        capture.frames.trySend(pcm(1))
        assertEquals(2, turnStarts.size)
    }

    @Test
    fun `a push-to-talk turn closes the wake word mic too`() = runWakeTest {
        val wake = controller()
        wake.start()
        voice.startListening()
        assertEquals(WakeState.InTurn, wake.state.value)
        voice.cancel()
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
    }

    @Test
    fun `no connection means back to listening`() = runWakeTest {
        connection.state.value = LinkState.Waiting("down", 1_000)
        val wake = controller()
        wake.start()
        sayWakeWord()

        assertEquals(1, feedback.wakes.size)
        assertTrue(turnStarts.isEmpty())
        assertEquals("Not connected to the server", voice.state.value.error)
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
        assertTrue(capture.open)
    }

    @Test
    fun `pauses for calls, other apps' recordings and Do Not Disturb`() = runWakeTest {
        val wake = controller()
        wake.start()

        signals.inCall.value = true
        assertEquals(WakeState.Paused(PauseReason.Call), wake.state.value)
        assertFalse(capture.open)
        signals.inCall.value = false
        assertTrue(capture.open)

        signals.otherAppRecording.value = true
        assertEquals(WakeState.Paused(PauseReason.MicInUse), wake.state.value)
        assertFalse(capture.open)
        signals.otherAppRecording.value = false

        signals.doNotDisturb.value = true
        assertEquals(WakeState.Paused(PauseReason.DoNotDisturb), wake.state.value)
        assertFalse(capture.open)
        settings.update { it.copy(pauseInDoNotDisturb = false) }
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
        assertTrue(capture.open)
    }

    @Test
    fun `pause and resume from the notification`() = runWakeTest {
        val wake = controller()
        wake.start()
        wake.pause()
        assertEquals(WakeState.Paused(PauseReason.User), wake.state.value)
        assertFalse(capture.open)
        wake.resume()
        assertTrue(capture.open)
    }

    @Test
    fun `off in settings means no mic`() = runWakeTest {
        settings.settings.value = WakeWordSettings(enabled = false)
        val wake = controller()
        wake.start()
        assertEquals(WakeState.Off, wake.state.value)
        assertFalse(capture.open)
        assertTrue(loaded.isEmpty())

        settings.update { it.copy(enabled = true) }
        assertTrue(capture.open)
    }

    @Test
    fun `stop closes the mic and the models`() = runWakeTest {
        val wake = controller()
        wake.start()
        wake.stop()
        assertEquals(WakeState.Off, wake.state.value)
        assertFalse(capture.open)
        assertTrue(models.closed)
    }

    @Test
    fun `sensitivity sets the threshold and a new model is loaded`() = runWakeTest {
        models.defaultScore = 0.6f
        settings.settings.value = WakeWordSettings(enabled = true, sensitivity = 0f) // threshold 0.8
        val wake = controller()
        wake.start()
        sayWakeWord()
        assertTrue(feedback.wakes.isEmpty())

        settings.update { it.copy(sensitivity = 1f) } // threshold 0.2
        sayWakeWord()
        assertEquals(1, feedback.wakes.size)
        voice.cancel()

        settings.update { it.copy(model = StockWakeWord.Alexa) }
        assertEquals(listOf(StockWakeWord.HeyJarvis, StockWakeWord.Alexa), loaded)
        assertEquals(WakeState.Listening(StockWakeWord.Alexa), wake.state.value)
    }

    @Test
    fun `chime follows the setting`() = runWakeTest {
        settings.settings.value = WakeWordSettings(enabled = true, chime = false)
        val wake = controller()
        wake.start()
        sayWakeWord()
        assertEquals(listOf(false), feedback.wakes)
    }

    @Test
    fun `that wasn't me logs a false accept and stops the turn`() = runWakeTest {
        val wake = controller()
        wake.start()
        sayWakeWord()
        wake.reportFalseAccept()

        assertEquals(listOf(WakeEvent.Kind.Detection, WakeEvent.Kind.FalseAccept), events.events.map { it.kind })
        assertTrue(connection.sent.any { it is ClientMessage.TurnCancel })
        assertNull(wake.recentWake.value)
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)

        // Nothing to report any more.
        wake.reportFalseAccept()
        assertEquals(2, events.events.size)
    }

    @Test
    fun `that wasn't me is only offered for a while`() = runWakeTest {
        val wake = controller()
        wake.start()
        sayWakeWord()
        assertNotNull(wake.recentWake.value)
        advanceTimeBy(WakeWordController.FALSE_ACCEPT_WINDOW_MILLIS + 1)
        assertNull(wake.recentWake.value)
    }

    @Test
    fun `a mic failure is shown and retried`() = runWakeTest {
        capture.error = SecurityException("no permission")
        val wake = controller()
        wake.start()
        assertEquals(WakeState.Failed("Alfred needs microphone permission"), wake.state.value)

        capture.error = null
        advanceTimeBy(WakeWordController.RETRY_MILLIS + 1)
        assertEquals(WakeState.Listening(StockWakeWord.HeyJarvis), wake.state.value)
        assertTrue(capture.open)
    }
}
