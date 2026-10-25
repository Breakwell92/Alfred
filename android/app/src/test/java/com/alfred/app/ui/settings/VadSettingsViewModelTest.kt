package com.alfred.app.ui.settings

import com.alfred.app.data.voice.Endpoint
import com.alfred.app.data.voice.vad.EndOfSpeechFactory
import com.alfred.app.data.voice.vad.EndpointRecord
import com.alfred.app.data.voice.vad.EndpointReport
import com.alfred.app.data.voice.vad.TurnMode
import com.alfred.app.data.voice.vad.VadConfig
import com.alfred.app.data.voice.vad.VadModel
import com.alfred.app.data.voice.vad.VadSettings
import com.alfred.app.testing.FakeEndpointLog
import com.alfred.app.testing.FakeVadSettingsRepository
import com.alfred.app.testing.LoudnessVadModel
import com.alfred.app.testing.MainDispatcherRule
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class VadSettingsViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val repository = FakeVadSettingsRepository()
    private val log = FakeEndpointLog()

    private fun TestScope.viewModel(load: () -> VadModel = ::LoudnessVadModel): VadSettingsViewModel {
        val factory = EndOfSpeechFactory(repository, log, load, backgroundScope, mainDispatcher.dispatcher, logcat = {})
        return VadSettingsViewModel(repository, factory, log).also { vm ->
            backgroundScope.launch(mainDispatcher.dispatcher) { vm.state.collect {} }
        }
    }

    @Test
    fun `shows the settings, the engine and the latest turns`() = runTest(mainDispatcher.dispatcher) {
        val report = EndpointReport(Endpoint.End, "silence", 2_000, 300, 1_280, 720)
        val record = EndpointRecord(5, TurnMode.PushToTalk, report, VadConfig())
        log.record(record)
        val vm = viewModel()

        val state = vm.state.value
        assertTrue(state.loaded)
        assertEquals("Silero VAD", state.engine)
        assertEquals(listOf(record), state.recent)
        assertEquals(1, log.loads)
    }

    @Test
    fun `changes are saved, kept in range, and reset`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()
        vm.setEndSilence(900)
        vm.setThreshold(0.99f)
        vm.setMinSpeech(10)
        vm.setUseSilero(false)

        val saved = repository.settings.value
        assertEquals(900, saved.config.endSilenceMillis)
        assertEquals(VadSettings.THRESHOLD_RANGE.endInclusive, saved.config.threshold)
        assertEquals(VadSettings.MIN_SPEECH_RANGE.first, saved.config.minSpeechMillis)
        assertEquals("Energy endpointer (Silero turned off)", vm.state.value.engine)

        vm.reset()
        assertEquals(VadSettings(), repository.settings.value)
    }

    @Test
    fun `says when silero couldn't load`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel { throw IllegalStateException("bad model") }
        assertEquals("Energy endpointer: Silero couldn't load (bad model)", vm.state.value.engine)
    }
}
