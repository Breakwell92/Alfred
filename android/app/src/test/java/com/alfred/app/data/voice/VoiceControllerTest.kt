package com.alfred.app.data.voice

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.server.AnswerResult
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.testing.FakeAudioCapture
import com.alfred.app.testing.FakeAudioPlayer
import com.alfred.app.testing.FakeDeviceConnection
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeServerClient
import com.alfred.app.testing.FakeSettingsRepository
import java.time.Instant
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class VoiceControllerTest {

    private val connection = FakeDeviceConnection()
    private val capture = FakeAudioCapture()
    private val player = FakeAudioPlayer()
    private val server = FakeServerClient()
    private val settings = FakeSettingsRepository(
        AppSettings("http://glados:8000", "token", pairedDeviceId = "paired-1", pairedDeviceName = "pixel"),
    )
    private val keys = FakeDeviceKeys()

    private fun TestScope.controller(): VoiceController {
        val dispatcher = UnconfinedTestDispatcher(testScheduler)
        return VoiceController(
            connection = connection,
            capture = capture,
            player = player,
            serverClient = server,
            settingsRepository = settings,
            signer = ApprovalSigner(keys) { 1_700_000_000 },
            scope = backgroundScope,
            ioDispatcher = dispatcher,
            clock = { NOW + testScheduler.currentTime },
        )
    }

    private fun runVoiceTest(block: suspend TestScope.() -> Unit) = runTest(UnconfinedTestDispatcher(), testBody = block)

    private fun turnId(): String = (connection.sent.first { it is ClientMessage.TurnStart } as ClientMessage.TurnStart).turnId

    private fun confirmRequest(turnId: String, tier: String = "write", expiresInMillis: Long = 120_000) =
        ServerMessage.ConfirmRequest(
            turnId = turnId,
            id = "c-1",
            summary = "Send a test message to Sam",
            tier = tier,
            expiresAt = Instant.ofEpochMilli(NOW + expiresInMillis).toString(),
        )

    @Test
    fun `a full turn`() = runVoiceTest {
        val voice = controller()

        assertTrue(voice.startListening())
        assertEquals(VoicePhase.Listening, voice.state.value.phase)
        assertTrue(capture.open)
        capture.frames.send(byteArrayOf(1))
        capture.frames.send(byteArrayOf(2))
        assertEquals(2, connection.audio.size)

        voice.stopListening()
        val id = turnId()
        assertEquals(ClientMessage.TurnEnd(id), connection.sent.last())
        assertFalse("the mic closes at end of speech", capture.open)
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)

        connection.receive(ServerMessage.TranscriptFinal(id, "What time is it?"))
        connection.receive(ServerMessage.ReplyTextDelta(id, "It's "))
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))
        connection.receiveAudio(id, byteArrayOf(7, 7))
        connection.receive(ServerMessage.ReplyTextDelta(id, "three."))
        assertEquals(VoicePhase.Speaking, voice.state.value.phase)
        connection.receive(ServerMessage.Metadata(TurnMetadata(id, specialist = "general", alias = "local-main")))
        connection.receive(ServerMessage.ReplyAudioEnd(id))

        assertEquals(listOf("begin(24000)", "finish"), player.calls)
        assertArrayEquals(byteArrayOf(7, 7), player.written.single())
        assertEquals(VoicePhase.Speaking, voice.state.value.phase)
        player.finishPlaying()

        val state = voice.state.value
        assertEquals(VoicePhase.Idle, state.phase)
        assertEquals("What time is it?", state.transcript)
        assertEquals("It's three.", state.reply)
        assertEquals("local-main", state.metadata?.alias)
        assertNull(state.error)
    }

    @Test
    fun `audio and messages for other turns are dropped`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()

        connection.receive(ServerMessage.ReplyTextDelta("old-turn", "stale"))
        connection.receiveAudio("old-turn", byteArrayOf(1))

        assertEquals("", voice.state.value.reply)
        assertTrue(player.written.isEmpty())
    }

    @Test
    fun `not connected asks for a reconnect`() = runVoiceTest {
        connection.state.value = LinkState.Waiting("down", 1_000)
        val voice = controller()

        assertFalse(voice.startListening())

        assertEquals(1, connection.reconnects)
        assertEquals("Not connected to the server", voice.state.value.error)
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
    }

    @Test
    fun `voice off on the server`() = runVoiceTest {
        connection.state.value = LinkState.Connected("s", voice = false)
        val voice = controller()

        assertFalse(voice.startListening())
        assertEquals("Voice is turned off on the server", voice.state.value.error)
    }

    @Test
    fun `cancel while speaking stops playback and tells the server`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()
        val id = turnId()
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))

        voice.cancel()

        assertEquals("stop", player.calls.last())
        assertEquals(ClientMessage.TurnCancel(id), connection.sent.last())
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        // Late audio for the cancelled turn is ignored.
        connection.receiveAudio(id, byteArrayOf(1))
        assertTrue(player.written.isEmpty())
    }

    @Test
    fun `cancel after the server finished only stops playback`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()
        val id = turnId()
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))
        connection.receive(ServerMessage.ReplyAudioEnd(id))
        val sent = connection.sent.size

        voice.cancel()

        assertEquals(sent, connection.sent.size)
        assertEquals("stop", player.calls.last())
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
    }

    @Test
    fun `losing the connection mid-turn ends it with an error`() = runVoiceTest {
        val voice = controller()
        voice.startListening()

        connection.disconnect()

        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertEquals("Connection lost", voice.state.value.error)
        assertFalse(capture.open)
    }

    @Test
    fun `a turn error is shown`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()

        connection.receive(ServerMessage.Error(turnId(), "no_speech", "Nothing recognised"))

        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertEquals("Didn't catch that", voice.state.value.error)
    }

    @Test
    fun `an error before end of speech closes the mic`() = runVoiceTest {
        val voice = controller()
        voice.startListening()

        connection.receive(ServerMessage.Error(turnId(), "audio_too_long", "too long"))

        assertFalse(capture.open)
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
    }

    @Test
    fun `a mic failure cancels the turn`() = runVoiceTest {
        capture.error = SecurityException("no permission")
        val voice = controller()

        voice.startListening()

        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertEquals("Alfred needs microphone permission", voice.state.value.error)
        assertTrue(connection.sent.last() is ClientMessage.TurnCancel)
    }

    @Test
    fun `write confirmation is answered over the socket once the asking turn ends`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()
        val id = turnId()
        connection.receive(ServerMessage.ReplyAudioStart(id, 24_000))
        connection.receive(confirmRequest(id))
        assertEquals("c-1", voice.state.value.confirmation?.id)

        voice.answer(approve = true)

        assertTrue("the sheet waits, buttons off", voice.state.value.confirmation!!.answering)
        assertEquals("playback of the question stops", "stop", player.calls.last())
        assertFalse(connection.sent.any { it is ClientMessage.ConfirmReply })

        connection.receive(ServerMessage.Metadata(TurnMetadata(id)))
        connection.receive(ServerMessage.ReplyAudioEnd(id))
        assertNull(voice.state.value.confirmation)

        val reply = connection.sent.last() as ClientMessage.ConfirmReply
        assertEquals("c-1", reply.confirmationId)
        assertTrue(reply.approve)
        val answerTurn = reply.turnId!!
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)

        // The spoken answer is its own turn.
        connection.receive(ServerMessage.ReplyTextDelta(answerTurn, "Sent."))
        connection.receive(ServerMessage.ReplyAudioStart(answerTurn, 24_000))
        connection.receive(ServerMessage.ReplyAudioEnd(answerTurn))
        player.finishPlaying()
        assertEquals("Sent.", voice.state.value.reply)
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
    }

    @Test
    fun `write confirmation after the turn is sent straight away`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()
        val id = turnId()
        connection.receive(confirmRequest(id))
        connection.receive(ServerMessage.ReplyAudioEnd(id))

        voice.answer(approve = false)

        val reply = connection.sent.last() as ClientMessage.ConfirmReply
        assertFalse(reply.approve)
    }

    @Test
    fun `a write answer that can't be sent comes back to the sheet`() = runVoiceTest {
        val voice = controller()
        connection.receive(confirmRequest("t-x"))
        connection.state.value = LinkState.Waiting("down", 1_000)

        voice.answer(approve = true)

        val confirmation = voice.state.value.confirmation
        assertNotNull(confirmation)
        assertFalse(confirmation!!.answering)
        assertEquals("Not connected to the server; try again", voice.state.value.error)

        connection.state.value = LinkState.Connected("s", voice = true)
        voice.answer(approve = true)
        assertTrue(connection.sent.last() is ClientMessage.ConfirmReply)
        assertNull(voice.state.value.confirmation)
    }

    @Test
    fun `a held-back write answer lost to a disconnect comes back to the sheet`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        voice.stopListening()
        connection.receive(confirmRequest(turnId()))
        voice.answer(approve = true)

        connection.disconnect()

        assertFalse(connection.sent.any { it is ClientMessage.ConfirmReply })
        val confirmation = voice.state.value.confirmation
        assertNotNull(confirmation)
        assertFalse(confirmation!!.answering)
        assertNotNull(voice.state.value.error)
    }

    @Test
    fun `irreversible confirmation is approved over http with a signed proof`() = runVoiceTest {
        server.answer = AnswerResult.Answered("The front door is locked.")
        val voice = controller()
        connection.receive(confirmRequest("t-x", tier = "irreversible"))

        voice.answer(approve = true)

        val answer = server.answered.single()
        assertEquals("c-1", answer.confirmationId)
        assertTrue(answer.approve)
        assertEquals("paired-1", answer.proof?.deviceId)
        assertFalse(connection.sent.any { it is ClientMessage.ConfirmReply })
        assertNull(voice.state.value.confirmation)
        assertEquals("The front door is locked.", voice.state.value.reply)
    }

    @Test
    fun `irreversible approval needs a paired phone`() = runVoiceTest {
        val unpaired = FakeSettingsRepository(AppSettings("http://glados:8000", "token"))
        val voice = VoiceController(
            connection, capture, player, server, unpaired, ApprovalSigner(keys),
            backgroundScope, UnconfinedTestDispatcher(testScheduler),
            clock = { NOW + testScheduler.currentTime },
        )
        connection.receive(confirmRequest("t-x", tier = "irreversible"))

        voice.answer(approve = true)

        assertTrue(server.answered.isEmpty())
        assertNotNull("the sheet stays up", voice.state.value.confirmation)
        assertFalse(voice.state.value.confirmation!!.answering)
        assertEquals("Pair this phone in Settings → Approvals to approve this", voice.state.value.error)
    }

    @Test
    fun `irreversible deny needs no proof`() = runVoiceTest {
        val voice = controller()
        connection.receive(confirmRequest("t-x", tier = "irreversible"))

        voice.answer(approve = false)

        val answer = server.answered.single()
        assertFalse(answer.approve)
        assertNull(answer.proof)
    }

    @Test
    fun `a refused approval keeps the sheet up with the reason`() = runVoiceTest {
        server.answer = AnswerResult.Forbidden("Not paired")
        val voice = controller()
        connection.receive(confirmRequest("t-x", tier = "irreversible"))

        voice.answer(approve = true)

        assertNotNull(voice.state.value.confirmation)
        assertEquals("Not paired", voice.state.value.error)
    }

    @Test
    fun `a confirmation disappears when it expires`() = runVoiceTest {
        val voice = controller()
        connection.receive(confirmRequest("t-x", expiresInMillis = 5_000))

        advanceTimeBy(4_000)
        assertNotNull(voice.state.value.confirmation)
        advanceTimeBy(1_001)
        assertNull(voice.state.value.confirmation)
    }

    @Test
    fun `a new turn replaces the pending confirmation`() = runVoiceTest {
        val voice = controller()
        connection.receive(confirmRequest("t-x"))

        voice.startListening()

        assertNull(voice.state.value.confirmation)
    }

    @Test
    fun `an end-of-speech detector ends a hands-free turn`() = runVoiceTest {
        val voice = controller()
        var frames = 0
        assertTrue(voice.startListening(endOfSpeech = { ++frames >= 3 }))
        repeat(2) { capture.frames.trySend(ByteArray(640)) }
        assertEquals(VoicePhase.Listening, voice.state.value.phase)

        capture.frames.trySend(ByteArray(640))
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)
        assertEquals(ClientMessage.TurnEnd(turnId()), connection.sent.last())
        assertEquals(3, connection.audio.size)
        assertFalse(capture.open)
    }

    @Test
    fun `without a detector silence doesn't end a turn`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        repeat(500) { capture.frames.trySend(ByteArray(640)) }
        assertEquals(VoicePhase.Listening, voice.state.value.phase)
    }

    @Test
    fun `level follows the mic while listening`() = runVoiceTest {
        val voice = controller()
        val loud = pcm(*IntArray(160) { if (it % 2 == 0) 20_000 else -20_000 })
        val quiet = pcm(*IntArray(160) { if (it % 2 == 0) 400 else -400 })
        assertEquals(0f, voice.level.value, 0f)

        voice.startListening()
        capture.frames.send(loud)
        assertEquals(pcm16Level(loud), voice.level.value, 0f)
        assertEquals(1f, voice.level.value, 0f)
        capture.frames.send(quiet)
        assertEquals(pcm16Level(quiet), voice.level.value, 0f)

        voice.stopListening()
        assertEquals("the mic level drops when the mic closes", 0f, voice.level.value, 0f)
    }

    @Test
    fun `level follows playback while speaking, and only then`() = runVoiceTest {
        val voice = controller()
        player.level.value = 0.7f
        assertEquals("idle ignores the player", 0f, voice.level.value, 0f)

        voice.startListening()
        voice.stopListening()
        assertEquals("thinking is 0", 0f, voice.level.value, 0f)

        connection.receive(ServerMessage.ReplyAudioStart(turnId(), 24_000))
        assertEquals(VoicePhase.Speaking, voice.state.value.phase)
        assertEquals(0.7f, voice.level.value, 0f)
        player.level.value = 0.2f
        assertEquals(0.2f, voice.level.value, 0f)

        voice.cancel()
        player.level.value = 0.9f
        assertEquals(0f, voice.level.value, 0f)
    }

    @Test
    fun `level drops to 0 when the mic fails`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        capture.frames.send(pcm(20_000, -20_000))
        assertEquals(1f, voice.level.value, 0f)

        capture.frames.close(IllegalStateException("Microphone read failed"))

        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertEquals(0f, voice.level.value, 0f)
    }

    @Test
    fun `the meter follows each frame`() = runVoiceTest {
        capture.activeEffects = setOf(CaptureEffect.NoiseSuppression)
        val voice = controller()
        voice.startListening()
        assertEquals(NOW, voice.state.value.listeningSinceMillis)

        capture.frames.send(tone(amplitude = 16_384))
        val loud = voice.meter.value
        assertEquals(-6.02f, loud.dbfs, 0.05f)
        assertEquals(1f, loud.level, 0f)
        assertTrue(loud.noiseFilter)
        assertEquals(INPUT_SAMPLE_RATE, loud.sampleRate)
        capture.frames.send(ByteArray(FRAME_BYTES))
        assertEquals(MIN_DBFS, voice.meter.value.dbfs, 0f)
        assertEquals(2L, voice.meter.value.frames)

        // A new turn starts the meter afresh.
        voice.cancel()
        voice.startListening()
        assertEquals(0L, voice.meter.value.frames)
    }

    @Test
    fun `partial transcripts show while speaking and give way to the final one`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        val id = turnId()

        connection.receive(ServerMessage.TranscriptPartial(id, stable = "", unstable = "Set a"))
        connection.receive(ServerMessage.TranscriptPartial(id, stable = "Set a", unstable = "timer"))
        connection.receive(ServerMessage.TranscriptPartial("other-turn", stable = "nope", unstable = ""))
        assertEquals("Set a", voice.state.value.partialStable)
        assertEquals("timer", voice.state.value.partialUnstable)

        voice.stopListening()
        assertNull(voice.state.value.listeningSinceMillis)
        connection.receive(ServerMessage.TranscriptFinal(id, "Set a timer for ten minutes."))
        val state = voice.state.value
        assertEquals("Set a timer for ten minutes.", state.transcript)
        assertEquals("", state.partialStable)
        assertEquals("", state.partialUnstable)
    }

    @Test
    fun `a turn is sent when it reaches the server's limit`() = runVoiceTest {
        connection.state.value = LinkState.Connected("session-1", voice = true, maxAudioSeconds = 10.0)
        val voice = controller()
        voice.startListening()
        val id = turnId()

        advanceTimeBy(9_499)
        assertEquals(VoicePhase.Listening, voice.state.value.phase)
        advanceTimeBy(2)
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)
        assertEquals(ClientMessage.TurnEnd(id), connection.sent.last())
    }

    @Test
    fun `turns are capped at a minute and default to the server's usual limit`() = runVoiceTest {
        connection.state.value = LinkState.Connected("session-1", voice = true, maxAudioSeconds = 600.0)
        val voice = controller()
        voice.startListening()
        advanceTimeBy(VoiceController.MAX_TURN_MILLIS + 1)
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)

        connection.state.value = LinkState.Connected("session-1", voice = true)
        voice.cancel()
        voice.startListening()
        advanceTimeBy(VoiceController.DEFAULT_MAX_TURN_MILLIS + 1)
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)
    }

    @Test
    fun `cancelling while listening never sends the turn to the router`() = runVoiceTest {
        val voice = controller()
        voice.startListening()
        val id = turnId()
        capture.frames.send(tone(amplitude = 8_000))

        voice.cancel()

        assertEquals(ClientMessage.TurnCancel(id), connection.sent.last())
        assertTrue(connection.sent.none { it is ClientMessage.TurnEnd })
        assertFalse(capture.open)
        assertEquals(VoicePhase.Idle, voice.state.value.phase)
        assertNull(voice.state.value.listeningSinceMillis)
        // The max-length timer died with the turn.
        advanceTimeBy(VoiceController.MAX_TURN_MILLIS + 1)
        assertTrue(connection.sent.none { it is ClientMessage.TurnEnd })
    }

    /** One 20 ms frame of a square wave at ±[amplitude]. */
    private fun tone(amplitude: Int): ByteArray = ByteArray(FRAME_BYTES).also { bytes ->
        for (i in 0 until FRAME_BYTES / 2) {
            val value = if (i % 2 == 0) amplitude else -amplitude
            bytes[2 * i] = (value and 0xFF).toByte()
            bytes[2 * i + 1] = (value shr 8 and 0xFF).toByte()
        }
    }

    private companion object {
        const val NOW = 1_700_000_000_000L
    }
}
