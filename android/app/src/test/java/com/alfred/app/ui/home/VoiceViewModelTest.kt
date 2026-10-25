package com.alfred.app.ui.home

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.ClientMessage
import com.alfred.app.data.voice.FRAME_BYTES
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.ServerMessage
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.testing.FakeAudioCapture
import com.alfred.app.testing.FakeAudioPlayer
import com.alfred.app.testing.FakeDeviceConnection
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeServerClient
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class VoiceViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val connection = FakeDeviceConnection()
    private val player = FakeAudioPlayer()
    private val capture = FakeAudioCapture()

    private fun TestScope.viewModel(): Pair<VoiceViewModel, VoiceController> {
        val controller = VoiceController(
            connection = connection,
            capture = capture,
            player = player,
            serverClient = FakeServerClient(),
            settingsRepository = FakeSettingsRepository(AppSettings("http://glados:8000", "token")),
            signer = ApprovalSigner(FakeDeviceKeys()),
            scope = backgroundScope,
            ioDispatcher = mainDispatcher.dispatcher,
        )
        return VoiceViewModel(controller, connection) to controller
    }

    private val phaseOf: (VoiceController) -> VoicePhase = { it.state.value.phase }

    @Test
    fun `hold to talk sends on release`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()

        vm.onTalkPressed()
        assertEquals(VoicePhase.Listening, phaseOf(voice))
        vm.onTalkReleased(heldMillis = 1_500)

        assertEquals(VoicePhase.Thinking, phaseOf(voice))
        assertTrue(connection.sent.last() is ClientMessage.TurnEnd)
    }

    @Test
    fun `a tapped turn sends itself once the speaker goes quiet`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        vm.onTalkPressed()
        vm.onTalkReleased(heldMillis = 100)

        // Nobody's spoken yet: room noise doesn't end it.
        repeat(400) { capture.frames.trySend(square(30)) }
        assertEquals(VoicePhase.Listening, phaseOf(voice))
        // Speech, then a second of quiet, does.
        repeat(25) { capture.frames.trySend(square(8_000)) }
        repeat(60) { capture.frames.trySend(square(30)) }

        assertEquals(VoicePhase.Thinking, phaseOf(voice))
        assertTrue(connection.sent.last() is ClientMessage.TurnEnd)
    }

    @Test
    fun `going quiet doesn't end a turn while the button is held`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        vm.onTalkPressed()

        // "Set a timer for…", a 1.5 s pause to think, "…ten minutes", all while holding.
        repeat(50) { capture.frames.trySend(square(30)) }
        repeat(25) { capture.frames.trySend(square(8_000)) }
        repeat(75) { capture.frames.trySend(square(30)) }
        assertEquals(VoicePhase.Listening, phaseOf(voice))
        repeat(25) { capture.frames.trySend(square(8_000)) }
        assertEquals(VoicePhase.Listening, phaseOf(voice))
        assertTrue(connection.sent.none { it is ClientMessage.TurnEnd })

        vm.onTalkReleased(heldMillis = 4_000)

        assertEquals(VoicePhase.Thinking, phaseOf(voice))
        assertTrue(connection.sent.last() is ClientMessage.TurnEnd)
    }

    @Test
    fun `a tap starts and a second tap sends`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()

        vm.onTalkPressed()
        vm.onTalkReleased(heldMillis = 100)
        assertEquals(VoicePhase.Listening, phaseOf(voice))

        vm.onTalkPressed()
        vm.onTalkReleased(heldMillis = 100)
        assertEquals(VoicePhase.Thinking, phaseOf(voice))
    }

    @Test
    fun `a tap while speaking stops playback`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        vm.onTalkPressed()
        vm.onTalkReleased(heldMillis = 1_500)
        val turnId = (connection.sent.first() as ClientMessage.TurnStart).turnId
        connection.receive(ServerMessage.ReplyAudioStart(turnId, 24_000))
        assertEquals(VoicePhase.Speaking, phaseOf(voice))

        vm.onTalkPressed()
        vm.onTalkReleased(heldMillis = 50)

        assertEquals(VoicePhase.Idle, phaseOf(voice))
        assertEquals("stop", player.calls.last())
    }

    @Test
    fun `ui state combines the turn and the link`() = runTest(mainDispatcher.dispatcher) {
        val (vm, _) = viewModel()
        backgroundScope.launch { vm.state.collect {} }

        connection.state.value = LinkState.Waiting("down", 2_000)

        assertEquals(LinkState.Waiting("down", 2_000), vm.state.value.link)
        assertEquals(VoicePhase.Idle, vm.state.value.voice.phase)
    }

    @Test
    fun `level comes from the controller`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        vm.onTalkPressed()
        vm.onTalkReleased(heldMillis = 1_500)
        connection.receive(ServerMessage.ReplyAudioStart((connection.sent.first() as ClientMessage.TurnStart).turnId, 24_000))

        player.level.value = 0.4f

        assertEquals(VoicePhase.Speaking, phaseOf(voice))
        assertEquals(0.4f, vm.level.value, 0f)
    }

    /** 20 ms of a square wave at ±[amplitude]: 30 is room noise, 8000 speech. */
    private fun square(amplitude: Int): ByteArray = ByteArray(FRAME_BYTES).also { bytes ->
        for (i in 0 until FRAME_BYTES / 2) {
            val value = if (i % 2 == 0) amplitude else -amplitude
            bytes[2 * i] = (value and 0xFF).toByte()
            bytes[2 * i + 1] = (value shr 8 and 0xFF).toByte()
        }
    }

    @Test
    fun `tapping the disabled mic retries the link now`() = runTest(mainDispatcher.dispatcher) {
        val (vm, _) = viewModel()
        val before = connection.reconnects

        vm.reconnect()

        assertEquals(before + 1, connection.reconnects)
    }
}
