package com.alfred.app.ui.home

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.ClientMessage
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

    private fun TestScope.viewModel(): Pair<VoiceViewModel, VoiceController> {
        val controller = VoiceController(
            connection = connection,
            capture = FakeAudioCapture(),
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
}
