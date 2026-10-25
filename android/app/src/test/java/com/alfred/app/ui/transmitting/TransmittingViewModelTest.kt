package com.alfred.app.ui.transmitting

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.ClientMessage
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.ServerMessage
import com.alfred.app.data.voice.SpokenWord
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.testing.FakeAudioCapture
import com.alfred.app.testing.FakeAudioPlayer
import com.alfred.app.testing.FakeDeviceConnection
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeServerClient
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class TransmittingViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val connection = FakeDeviceConnection()
    private val capture = FakeAudioCapture()
    private val player = FakeAudioPlayer()

    private fun TestScope.viewModel(): Pair<TransmittingViewModel, VoiceController> {
        val controller = VoiceController(
            connection = connection,
            capture = capture,
            player = player,
            serverClient = FakeServerClient(),
            settingsRepository = FakeSettingsRepository(AppSettings("http://glados:8000", "token")),
            signer = ApprovalSigner(FakeDeviceKeys()),
            scope = backgroundScope,
            ioDispatcher = mainDispatcher.dispatcher,
            clock = { 1_000L },
        )
        return TransmittingViewModel(controller, holdMillis = HOLD) to controller
    }

    /** Listens, ends the turn and returns its id: the controller is Thinking. */
    private fun VoiceController.askSomething(): String {
        startListening()
        stopListening()
        return (connection.sent.last { it is ClientMessage.TurnStart } as ClientMessage.TurnStart).turnId
    }

    @Test
    fun `comes up while thinking and speaking, holds, then gives way to Home`() =
        runTest(mainDispatcher.dispatcher) {
            val (vm, voice) = viewModel()
            assertFalse(vm.state.value.visible)

            voice.startListening()
            assertFalse("Recording has the screen while listening", vm.state.value.visible)

            voice.stopListening()
            val id = (connection.sent.first { it is ClientMessage.TurnStart } as ClientMessage.TurnStart).turnId
            assertTrue(vm.state.value.visible)
            assertEquals(VoicePhase.Thinking, vm.state.value.phase)
            assertEquals(Highlight.Pending, vm.state.value.highlight)

            connection.receive(ServerMessage.ReplyTextDelta(id, "Hello sir."))
            connection.receive(ServerMessage.ReplyWords(id, listOf(SpokenWord("Hello", 0, 300), SpokenWord("sir.", 300, 600))))
            connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))
            assertEquals(VoicePhase.Speaking, vm.state.value.phase)
            assertEquals(Highlight.FollowAudio, vm.state.value.highlight)
            assertEquals(listOf("Hello", "sir."), vm.state.value.spans.map { it.word.word })

            connection.receive(ServerMessage.ReplyAudioEnd(id))
            player.finishPlaying()
            assertEquals(VoicePhase.Idle, vm.state.value.phase)
            assertEquals(Highlight.Plain, vm.state.value.highlight)
            assertTrue("the finished reply stays up for a moment", vm.state.value.visible)

            advanceTimeBy(HOLD + 1)
            assertFalse(vm.state.value.visible)
        }

    @Test
    fun `heard position only moves forward within a turn`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        voice.askSomething()

        player.position.value = 400
        assertEquals(400L, vm.heard.value)
        // Playback ending resets the player's position before the turn ends.
        player.position.value = 0
        assertEquals(400L, vm.heard.value)

        voice.askSomething()
        assertEquals("a new turn starts from the top", 0L, vm.heard.value)
    }

    @Test
    fun `close stops Alfred and goes straight back`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        val id = voice.askSomething()
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))

        vm.close()

        assertFalse(vm.state.value.visible)
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertEquals(ClientMessage.TurnCancel(id), connection.sent.last())
        assertEquals("stop", player.calls.last())
    }

    @Test
    fun `cut in hands over to a new recording`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        val id = voice.askSomething()
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))

        vm.cutIn()

        assertEquals(VoicePhase.Listening, voice.state.value.phase)
        assertFalse(vm.state.value.visible)
        assertTrue(connection.sent.contains(ClientMessage.TurnCancel(id)))
        assertTrue(capture.open)
    }

    @Test
    fun `cut in with no link shows why for the hold`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        val id = voice.askSomething()
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))
        connection.state.value = LinkState.Waiting("down", 1_000)

        vm.cutIn()

        assertTrue(vm.state.value.visible)
        assertEquals("Not connected to the server", vm.state.value.error)
        advanceTimeBy(HOLD + 1)
        assertFalse(vm.state.value.visible)
    }

    private companion object {
        const val HOLD = 2_000L
    }
}
