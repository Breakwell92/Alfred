package com.alfred.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.settings.isValidApiToken
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SettingsUiState(
    val serverUrl: String = "",
    val apiToken: String = "",
    val loaded: Boolean = false,
    val saved: Boolean = false,
    /** Set when Save was refused because the token can't be sent in an HTTP header. */
    val tokenError: String? = null,
)

class SettingsViewModel(private val repository: SettingsRepository) : ViewModel() {

    private val _state = MutableStateFlow(SettingsUiState())
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val current = repository.settings.first()
            _state.update {
                it.copy(serverUrl = current.serverUrl, apiToken = current.apiToken, loaded = true)
            }
        }
    }

    fun onServerUrlChange(value: String) = _state.update { it.copy(serverUrl = value, saved = false) }

    fun onApiTokenChange(value: String) =
        _state.update { it.copy(apiToken = value, saved = false, tokenError = null) }

    fun save() {
        val current = _state.value
        val settings = AppSettings(
            serverUrl = current.serverUrl.trim().trimEnd('/'),
            apiToken = current.apiToken.trim(),
        )
        // An empty token is allowed (Home shows "Not set up"); anything else must be header-safe.
        if (settings.apiToken.isNotEmpty() && !isValidApiToken(settings.apiToken)) {
            _state.update {
                it.copy(saved = false, tokenError = "Token can only contain visible ASCII characters")
            }
            return
        }
        viewModelScope.launch {
            repository.save(settings)
            _state.update {
                it.copy(
                    serverUrl = settings.serverUrl,
                    apiToken = settings.apiToken,
                    saved = true,
                    tokenError = null,
                )
            }
        }
    }
}
