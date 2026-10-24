package com.alfred.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Polls `/health` and `/me` while the Home screen is visible (collectors present), and starts
 * over straight away when settings change or [refresh] is called.
 */
class HomeViewModel(
    settingsRepository: SettingsRepository,
    private val serverClient: ServerClient,
    private val pollIntervalMillis: Long = DEFAULT_POLL_INTERVAL_MILLIS,
) : ViewModel() {

    private val refreshRequests = Channel<Unit>(Channel.CONFLATED)

    @OptIn(ExperimentalCoroutinesApi::class)
    val status: StateFlow<ConnectionStatus> = settingsRepository.settings
        .distinctUntilChanged()
        .flatMapLatest(::poll)
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
        while (true) {
            emit(serverClient.checkConnection(settings))
            withTimeoutOrNull(pollIntervalMillis) { refreshRequests.receive() }
        }
    }

    companion object {
        const val DEFAULT_POLL_INTERVAL_MILLIS = 10_000L
        private const val STOP_TIMEOUT_MILLIS = 5_000L
    }
}
