package com.alfred.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.ProfileSettings
import com.alfred.app.data.settings.ProfileSettingsRepository
import com.alfred.app.data.settings.SettingsRepository
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.retryWhen
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Polls `/health` and `/me` while the Home screen is visible (collectors present), and starts
 * over straight away when settings change or [refresh] is called. Failures (a check that
 * throws, unreadable settings) become an error status; they never reach `viewModelScope`.
 * Each time the server comes (back) up it also asks for the active persona and caches it in
 * [profile], so the PROTOCOL tile keeps the last known name offline.
 */
class HomeViewModel(
    settingsRepository: SettingsRepository,
    private val serverClient: ServerClient,
    private val profileRepository: ProfileSettingsRepository,
    private val pollIntervalMillis: Long = DEFAULT_POLL_INTERVAL_MILLIS,
) : ViewModel() {

    val profile: StateFlow<ProfileSettings> = profileRepository.settings
        .catch { emit(ProfileSettings()) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS), ProfileSettings())

    private val refreshRequests = Channel<Unit>(Channel.CONFLATED)

    @OptIn(ExperimentalCoroutinesApi::class)
    val status: StateFlow<ConnectionStatus> = settingsRepository.settings
        .distinctUntilChanged()
        .flatMapLatest(::poll)
        // Settings can't be read: show it, then try again on "Check now" or the next poll.
        .retryWhen { e, _ ->
            emit(ConnectionStatus.Unreachable("Couldn't read settings: ${e.describe()}"))
            withTimeoutOrNull(pollIntervalMillis) { refreshRequests.receive() }
            true
        }
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS),
            initialValue = ConnectionStatus.Checking,
        )

    fun refresh() {
        refreshRequests.trySend(Unit)
    }

    private fun poll(settings: AppSettings): Flow<ConnectionStatus> = flow {
        emit(ConnectionStatus.Checking)
        var wasConnected = false
        while (true) {
            val status = check(settings)
            emit(status)
            val connected = status == ConnectionStatus.Connected
            if (connected && !wasConnected) refreshPersona(settings)
            wasConnected = connected
            withTimeoutOrNull(pollIntervalMillis) { refreshRequests.receive() }
        }
    }

    /** Keeps the cached name when the server can't say (e.g. no personas yet, STU-66). */
    private suspend fun refreshPersona(settings: AppSettings) {
        try {
            val name = serverClient.activePersona(settings) ?: return
            profileRepository.update { it.copy(personaName = name) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // Best effort: the tile shows the cached or default name.
        }
    }

    private suspend fun check(settings: AppSettings): ConnectionStatus = try {
        serverClient.checkConnection(settings)
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        ConnectionStatus.Unreachable(e.describe())
    }

    private fun Throwable.describe(): String = message ?: javaClass.simpleName

    companion object {
        const val DEFAULT_POLL_INTERVAL_MILLIS = 10_000L
        private const val STOP_TIMEOUT_MILLIS = 5_000L
    }
}
