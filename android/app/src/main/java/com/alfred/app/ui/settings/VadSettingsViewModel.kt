package com.alfred.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.voice.vad.EndOfSpeechFactory
import com.alfred.app.data.voice.vad.EndpointLog
import com.alfred.app.data.voice.vad.EndpointRecord
import com.alfred.app.data.voice.vad.VadConfig
import com.alfred.app.data.voice.vad.VadEngineStatus
import com.alfred.app.data.voice.vad.VadSettings
import com.alfred.app.data.voice.vad.VadSettingsRepository
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class VadUiState(
    val settings: VadSettings = VadSettings(),
    val loaded: Boolean = false,
    /** Which endpointer turns use now, in words. */
    val engine: String = "",
    /** The latest turns' endpoints, newest first. */
    val recent: List<EndpointRecord> = emptyList(),
)

/** Settings → Developer → End of speech (hidden; ADR 0023). */
class VadSettingsViewModel(
    private val repository: VadSettingsRepository,
    factory: EndOfSpeechFactory,
    private val log: EndpointLog,
) : ViewModel() {

    val state: StateFlow<VadUiState> = combine(
        repository.settings,
        factory.status,
        log.recent,
    ) { settings, status, recent ->
        VadUiState(settings = settings, loaded = true, engine = describe(settings, status), recent = recent)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), VadUiState())

    init {
        viewModelScope.launch { log.load() }
    }

    fun setUseSilero(use: Boolean) = update { it.copy(useSilero = use) }

    fun setThreshold(threshold: Float) = updateConfig { it.copy(threshold = threshold) }

    fun setEndSilence(millis: Int) = updateConfig { it.copy(endSilenceMillis = millis) }

    fun setNoSpeech(millis: Int) = updateConfig { it.copy(noSpeechMillis = millis) }

    fun setMaxUtterance(millis: Int) = updateConfig { it.copy(maxUtteranceMillis = millis) }

    fun setMinSpeech(millis: Int) = updateConfig { it.copy(minSpeechMillis = millis) }

    fun reset() = update { VadSettings() }

    private fun updateConfig(transform: (VadConfig) -> VadConfig) = update { it.copy(config = transform(it.config)) }

    private fun update(transform: (VadSettings) -> VadSettings) {
        viewModelScope.launch { repository.update(transform) }
    }

    private fun describe(settings: VadSettings, status: VadEngineStatus): String = when {
        !settings.useSilero -> "Energy endpointer (Silero turned off)"
        status is VadEngineStatus.Ready -> "Silero VAD"
        status is VadEngineStatus.Failed -> "Energy endpointer: Silero couldn't load (${status.message})"
        else -> "Energy endpointer until Silero has loaded"
    }
}
