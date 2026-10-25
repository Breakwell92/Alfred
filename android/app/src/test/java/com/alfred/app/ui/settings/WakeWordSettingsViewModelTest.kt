package com.alfred.app.ui.settings

import com.alfred.app.data.wakeword.StockWakeWord
import com.alfred.app.data.wakeword.WakeCounts
import com.alfred.app.data.wakeword.WakeWordController
import com.alfred.app.data.wakeword.WakeWordSettings
import com.alfred.app.testing.FakeAudioCapture
import com.alfred.app.testing.FakeHealthRepository
import com.alfred.app.testing.FakeWakeEventLog
import com.alfred.app.testing.FakeWakeFeedback
import com.alfred.app.testing.FakeWakeSignals
import com.alfred.app.testing.FakeWakeWordModels
import com.alfred.app.testing.FakeWakeWordSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.testing.FakeAudioPlayer
import com.alfred.app.testing.FakeDeviceConnection
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeServerClient
import com.alfred.app.testing.FakeSettingsRepository
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class WakeWordSettingsViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val repository = FakeWakeWordSettingsRepository(WakeWordSettings())
    private val events = FakeWakeEventLog()
    private val health = FakeHealthRepository()

    private fun TestScope.viewModel(): WakeWordSettingsViewModel {
        val capture = FakeAudioCapture()
        val voice = VoiceController(
            FakeDeviceConnection(),
            capture,
            FakeAudioPlayer(),
            FakeServerClient(),
            FakeSettingsRepository(AppSettings("http://glados:8000", "token")),
            ApprovalSigner(FakeDeviceKeys()),
            backgroundScope,
        )
        val controller = WakeWordController(
            repository, voice, capture, FakeWakeSignals(), FakeWakeFeedback(), events,
            loadModels = { FakeWakeWordModels() },
            scope = backgroundScope,
            workDispatcher = mainDispatcher.dispatcher,
        )
        controller.start()
        return WakeWordSettingsViewModel(repository, controller, events, health).also {
            it.state.launchIn(backgroundScope)
        }
    }

    @Test
    fun `shows settings, status and counts`() = runTest(mainDispatcher.dispatcher) {
        events.counts.value = WakeCounts(detections = 4, falseAccepts = 1)
        val vm = viewModel()
        val state = vm.state.value
        assertTrue(state.loaded)
        assertFalse(state.settings.enabled)
        assertEquals("Off", state.status)
        assertEquals(WakeCounts(4, 1), state.counts)
        assertEquals(1, events.loads)

        vm.setEnabled(true)
        assertTrue(vm.state.value.settings.enabled)
        assertEquals("Listening for “Hey Jarvis”", vm.state.value.status)
    }

    @Test
    fun `saves each setting`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()
        vm.setSensitivity(1.5f)
        vm.setChime(false)
        vm.setModel(StockWakeWord.HeyMycroft)
        vm.setPauseInDoNotDisturb(false)
        assertEquals(
            WakeWordSettings(sensitivity = 1f, chime = false, model = StockWakeWord.HeyMycroft, pauseInDoNotDisturb = false),
            repository.settings.value,
        )
        assertEquals(0.2f, repository.settings.value.threshold, 1e-6f)
    }
}
