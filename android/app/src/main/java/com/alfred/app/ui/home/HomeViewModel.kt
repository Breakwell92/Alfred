package com.alfred.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.AppSettings
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
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Polls `/health` and `/me` while the Home screen is visible (collectors present), and starts
 * over straight away when settings change or [refresh] is called. Failures (a check that
 * throws, unreadable settings) become an error status; they never reach `viewModelScope`.
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
        .catch { e -> emit(ConnectionStatus.Unreachable("Couldn't read settings: ${e.describe()}")) }
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
            emit(check(settings))
            withTimeoutOrNull(pollIntervalMillis) { refreshRequests.receive() }
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
