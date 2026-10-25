package com.alfred.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.server.PairingResult
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.settings.isValidApiToken
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class SettingsUiState(
    val serverUrl: String = "",
    val apiToken: String = "",
    val loaded: Boolean = false,
    val saved: Boolean = false,
    /** Set when Save was refused because the token can't be sent in an HTTP header. */
    val tokenError: String? = null,
    /** The name the server gave this app when it was paired for approvals; null if not paired. */
    val pairedDeviceName: String? = null,
    val pairingCode: String = "",
    val pairing: Boolean = false,
    val pairingError: String? = null,
)

class SettingsViewModel(
    private val repository: SettingsRepository,
    private val serverClient: ServerClient,
    private val deviceKeys: DeviceKeys,
    /** Keystore calls block; tests run them on the test dispatcher. */
    private val keyDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    private val _state = MutableStateFlow(SettingsUiState())
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val current = repository.settings.first()
            _state.update {
                it.copy(
                    serverUrl = current.serverUrl,
                    apiToken = current.apiToken,
                    pairedDeviceName = current.pairedDeviceName,
                    loaded = true,
                )
            }
        }
    }

    fun onServerUrlChange(value: String) = _state.update { it.copy(serverUrl = value, saved = false) }

    fun onApiTokenChange(value: String) =
        _state.update { it.copy(apiToken = value, saved = false, tokenError = null) }

    fun onPairingCodeChange(value: String) =
        _state.update { it.copy(pairingCode = value, pairingError = null) }

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

    /**
     * Registers this app's approval key with the server, using a one-time code from
     * `python -m alfred_server.devices pair` and the saved server URL and token.
     */
    fun pair() {
        val code = _state.value.pairingCode.trim()
        if (code.isEmpty()) {
            _state.update { it.copy(pairingError = "Enter the code from the server") }
            return
        }
        _state.update { it.copy(pairing = true, pairingError = null) }
        viewModelScope.launch {
            val result = try {
                val publicKey = withContext(keyDispatcher) { deviceKeys.publicKey() }
                serverClient.pairDevice(repository.settings.first(), code, publicKey)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Keystore failures (provider errors, no usable key storage).
                PairingResult.Failed("Couldn't create the approval key: ${e.message ?: e.javaClass.simpleName}")
            }
            when (result) {
                is PairingResult.Paired -> {
                    repository.savePairing(result.deviceId, result.name)
                    _state.update {
                        it.copy(pairing = false, pairedDeviceName = result.name, pairingCode = "")
                    }
                }
                PairingResult.InvalidCode -> _state.update {
                    it.copy(pairing = false, pairingError = "That code is wrong, used or expired")
                }
                is PairingResult.Failed -> _state.update {
                    it.copy(pairing = false, pairingError = result.reason)
                }
            }
        }
    }
}
