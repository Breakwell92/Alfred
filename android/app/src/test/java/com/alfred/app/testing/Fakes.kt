package com.alfred.app.testing

import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.rules.TestWatcher
import org.junit.runner.Description

class FakeSettingsRepository(initial: AppSettings) : SettingsRepository {
    private val state = MutableStateFlow(initial)
    override val settings: StateFlow<AppSettings> = state

    override suspend fun save(settings: AppSettings) {
        state.value = settings
    }
}

/** Answers with [status]; records every settings value it was asked to check. */
class FakeServerClient(var status: ConnectionStatus = ConnectionStatus.Connected) : ServerClient {
    val checked = mutableListOf<AppSettings>()

    override suspend fun checkConnection(settings: AppSettings): ConnectionStatus {
        checked += settings
        return status
    }
}

/** Points `Dispatchers.Main` (used by `viewModelScope`) at a test dispatcher. */
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule(val dispatcher: TestDispatcher = UnconfinedTestDispatcher()) : TestWatcher() {
    override fun starting(description: Description) = Dispatchers.setMain(dispatcher)

    override fun finished(description: Description) = Dispatchers.resetMain()
}
