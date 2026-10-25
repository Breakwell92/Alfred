package com.alfred.app.ui.recording

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.ClientMessage
import com.alfred.app.data.voice.FRAME_BYTES
import com.alfred.app.data.voice.MicMeter
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
import com.alfred.app.ui.theme.AlfredDarkColors
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class RecordingViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val connection = FakeDeviceConnection()
    private val capture = FakeAudioCapture()

    private fun TestScope.viewModel(): Pair<RecordingViewModel, VoiceController> {
        val controller = VoiceController(
            connection = connection,
            capture = capture,
            player = FakeAudioPlayer(),
            serverClient = FakeServerClient(),
            settingsRepository = FakeSettingsRepository(AppSettings("http://glados:8000", "token")),
            signer = ApprovalSigner(FakeDeviceKeys()),
            scope = backgroundScope,
            ioDispatcher = mainDispatcher.dispatcher,
            clock = { 1_000L },
        )
        return RecordingViewModel(controller) to controller
    }

    @Test
    fun `shows while listening, with the live transcript`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        assertFalse(vm.state.value.listening)

        voice.startListening()
        val id = (connection.sent.single() as ClientMessage.TurnStart).turnId
        connection.receive(ServerMessage.TranscriptPartial(id, stable = "Set a", unstable = "timer"))

        assertEquals(RecordingUiState(listening = true, sinceMillis = 1_000L, stable = "Set a", unstable = "timer"), vm.state.value)

        vm.stop()
        assertFalse(vm.state.value.listening)
        assertEquals(VoicePhase.Thinking, voice.state.value.phase)
    }

    @Test
    fun `cancel drops the turn without sending it`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        voice.startListening()

        vm.cancel()

        assertFalse(vm.state.value.listening)
        assertTrue(connection.sent.last() is ClientMessage.TurnCancel)
        assertTrue(connection.sent.none { it is ClientMessage.TurnEnd })
    }

    @Test
    fun `the waveform scrolls with the mic and starts empty each turn`() = runTest(mainDispatcher.dispatcher) {
        val (vm, voice) = viewModel()
        voice.startListening()

        repeat(4) { capture.frames.send(loud()) }
        val bars = vm.bars.value
        assertEquals(48, bars.size)
        assertEquals(listOf(1f, 1f), bars.takeLast(2))
        assertEquals(0f, bars[bars.size - 3], 0f)

        voice.cancel()
        voice.startListening()
        assertTrue(vm.bars.value.all { it == 0f })
    }

    @Test
    fun `level history keeps the loudest frame of each bar, newest last`() {
        val history = LevelHistory(size = 3, framesPerBar = 2)
        listOf(0.1f, 0.4f, 0.9f, 0.2f, 0.3f, 0.3f, 0.5f).forEach(history::add)
        // The 7th frame is waiting for its pair.
        assertEquals(listOf(0.4f, 0.9f, 0.3f), history.bars())
        history.add(0.1f)
        assertEquals(listOf(0.9f, 0.3f, 0.5f), history.bars())
        history.clear()
        assertEquals(listOf(0f, 0f, 0f), history.bars())
    }

    @Test
    fun `telemetry line`() {
        assertEquals("−32 dB · NOISE FILTER · ON · 16 kHz", telemetryLine(MicMeter(dbfs = -32.4f, noiseFilter = true)))
        assertEquals("−96 dB · NOISE FILTER · OFF · 44.1 kHz", telemetryLine(MicMeter(sampleRate = 44_100)))
    }

    @Test
    fun `timer reads minutes and seconds`() {
        assertEquals("00:00", formatElapsed(0))
        assertEquals("00:04", formatElapsed(4_999))
        assertEquals("01:05", formatElapsed(65_000))
    }

    @Test
    fun `provisional words are dimmed`() {
        val colors = AlfredDarkColors
        val text = transcriptText("Set a", "timer for", colors)
        assertEquals("Set a timer for", text.text)
        val styles = text.spanStyles.associate { text.text.substring(it.start, it.end) to it.item.color }
        assertEquals(colors.textPrimary, styles["Set a"])
        assertEquals(colors.textDim, styles["timer for"])

        assertEquals("Listening…", transcriptText("", "", colors).text)
        assertEquals("Set a", transcriptText("Set a", "", colors).text)
    }

    private fun loud(): ByteArray = ByteArray(FRAME_BYTES).also { bytes ->
        for (i in 0 until FRAME_BYTES / 2) {
            val value = if (i % 2 == 0) 20_000 else -20_000
            bytes[2 * i] = (value and 0xFF).toByte()
            bytes[2 * i + 1] = (value shr 8 and 0xFF).toByte()
        }
    }
}
