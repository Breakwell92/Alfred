package com.alfred.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.reliability.HealthRepository
import com.alfred.app.data.reliability.ServiceHealth
import com.alfred.app.data.wakeword.StockWakeWord
import com.alfred.app.data.wakeword.WakeState
import com.alfred.app.data.wakeword.WakeCounts
import com.alfred.app.data.wakeword.WakeEventLog
import com.alfred.app.data.wakeword.WakeWordController
import com.alfred.app.data.wakeword.WakeWordSettings
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import com.alfred.app.data.wakeword.describe
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class WakeWordUiState(
    val settings: WakeWordSettings = WakeWordSettings(),
    val loaded: Boolean = false,
    /** What the listener is doing right now, e.g. "Listening for “Hey Jarvis”". */
    val status: String = "",
    val counts: WakeCounts = WakeCounts(),
    /** Whether the service is up (whatever it's doing), for "Listening since…". */
    val running: Boolean = false,
    val health: ServiceHealth = ServiceHealth(),
)

/** Settings → Wake word. The service is started and stopped by MainActivity to match [WakeWordSettings.enabled]. */
class WakeWordSettingsViewModel(
    private val repository: WakeWordSettingsRepository,
    controller: WakeWordController,
    private val events: WakeEventLog,
    health: HealthRepository,
) : ViewModel() {

    val state: StateFlow<WakeWordUiState> = combine(
        repository.settings,
        controller.state,
        events.counts,
        health.health,
    ) { settings, wake, counts, serviceHealth ->
        WakeWordUiState(
            settings = settings,
            loaded = true,
            status = wake.describe(),
            counts = counts,
            running = wake != WakeState.Off,
            health = serviceHealth,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), WakeWordUiState())

    init {
        viewModelScope.launch { events.load() }
    }

    /** Call with true only once the microphone permission is granted. */
    fun setEnabled(enabled: Boolean) = update { it.copy(enabled = enabled) }

    fun setSensitivity(sensitivity: Float) = update { it.copy(sensitivity = sensitivity.coerceIn(0f, 1f)) }

    fun setChime(chime: Boolean) = update { it.copy(chime = chime) }

    fun setModel(model: StockWakeWord) = update { it.copy(model = model) }

    fun setPauseInDoNotDisturb(pause: Boolean) = update { it.copy(pauseInDoNotDisturb = pause) }

    private fun update(transform: (WakeWordSettings) -> WakeWordSettings) {
        viewModelScope.launch { repository.update(transform) }
    }
}
