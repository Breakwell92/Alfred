package com.alfred.app.ui.home

import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.testing.FakeProfileSettingsRepository
import com.alfred.app.testing.FakeServerClient
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import kotlinx.coroutines.ExperimentalCoroutinesApi
import java.io.IOException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class HomeViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val initial = AppSettings(serverUrl = "http://glados:8000", apiToken = "token")
    private val settings = FakeSettingsRepository(initial)
    private val server = FakeServerClient()

    private val profile = FakeProfileSettingsRepository()

    private fun viewModel() = HomeViewModel(settings, server, profile, pollIntervalMillis = 10_000)

    @Test
    fun `shows the server's answer`() = runTest(mainDispatcher.dispatcher) {
        server.status = ConnectionStatus.Unauthorised
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }

        assertEquals(ConnectionStatus.Unauthorised, vm.status.value)
    }

    @Test
    fun `polls on an interval while observed`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }
        assertEquals(1, server.checked.size)

        advanceTimeBy(10_001)
        assertEquals(2, server.checked.size)

        server.status = ConnectionStatus.Unreachable("down")
        advanceTimeBy(10_000)
        assertEquals(3, server.checked.size)
        assertEquals(ConnectionStatus.Unreachable("down"), vm.status.value)
    }

    @Test
    fun `does not poll without observers`() = runTest(mainDispatcher.dispatcher) {
        viewModel()

        advanceTimeBy(60_000)

        assertEquals(0, server.checked.size)
    }

    @Test
    fun `rechecks straight away when settings change`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }
        val changed = initial.copy(apiToken = "new-token")

        settings.save(changed)
        runCurrent()

        assertEquals(listOf(initial, changed), server.checked)
    }

    @Test
    fun `refresh checks without waiting for the interval`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }

        vm.refresh()
        runCurrent()

        assertEquals(2, server.checked.size)
    }

    @Test
    fun `a check that throws becomes an error status and polling continues`() =
        runTest(mainDispatcher.dispatcher) {
            server.error = IllegalArgumentException("boom")
            val vm = viewModel()
            backgroundScope.launch { vm.status.collect {} }

            assertEquals(ConnectionStatus.Unreachable("boom"), vm.status.value)

            server.error = null
            advanceTimeBy(10_001)
            assertEquals(ConnectionStatus.Connected, vm.status.value)
        }

    @Test
    fun `unreadable settings become an error status and recover`() = runTest(mainDispatcher.dispatcher) {
        var readable = false
        val flaky = object : SettingsRepository {
            override val settings: Flow<AppSettings> = flow {
                if (!readable) throw IOException("corrupt")
                emit(initial)
            }

            override suspend fun save(settings: AppSettings) = Unit

            override suspend fun savePairing(deviceId: String, name: String) = Unit
        }
        val vm = HomeViewModel(flaky, server, profile, pollIntervalMillis = 10_000)
        backgroundScope.launch { vm.status.collect {} }
        assertEquals(ConnectionStatus.Unreachable("Couldn't read settings: corrupt"), vm.status.value)

        readable = true
        vm.refresh()
        runCurrent()
        assertEquals(ConnectionStatus.Connected, vm.status.value)
    }

    @Test
    fun `unreadable settings are retried on the poll interval`() = runTest(mainDispatcher.dispatcher) {
        var readable = false
        val flaky = object : SettingsRepository {
            override val settings: Flow<AppSettings> = flow {
                if (!readable) throw IOException("corrupt")
                emit(initial)
            }

            override suspend fun save(settings: AppSettings) = Unit

            override suspend fun savePairing(deviceId: String, name: String) = Unit
        }
        val vm = HomeViewModel(flaky, server, profile, pollIntervalMillis = 10_000)
        backgroundScope.launch { vm.status.collect {} }

        readable = true
        advanceTimeBy(10_001)
        assertEquals(ConnectionStatus.Connected, vm.status.value)
    }

    @Test
    fun `caches the active persona on every successful poll`() = runTest(mainDispatcher.dispatcher) {
        server.persona = "Butler"
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }
        backgroundScope.launch { vm.profile.collect {} }

        assertEquals("Butler", vm.profile.value.personaName)
        assertEquals(1, server.personaAsked)

        // Down: not asked.
        server.status = ConnectionStatus.Unreachable("down")
        advanceTimeBy(10_001)
        assertEquals(1, server.personaAsked)

        // Back up: asked again.
        server.status = ConnectionStatus.Connected
        advanceTimeBy(10_000)
        assertEquals(2, server.personaAsked)
    }

    @Test
    fun `a persona switched while connected shows on the next poll`() = runTest(mainDispatcher.dispatcher) {
        server.persona = "Butler"
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }
        backgroundScope.launch { vm.profile.collect {} }
        assertEquals("Butler", vm.profile.value.personaName)

        server.persona = "Dry"
        advanceTimeBy(10_001)

        assertEquals(ConnectionStatus.Connected, vm.status.value)
        assertEquals("Dry", vm.profile.value.personaName)
    }

    @Test
    fun `keeps the cached persona when the server can't say`() = runTest(mainDispatcher.dispatcher) {
        profile.update { it.copy(personaName = "Butler") }
        server.persona = null
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }
        backgroundScope.launch { vm.profile.collect {} }

        assertEquals(1, server.personaAsked)
        assertEquals("Butler", vm.profile.value.personaName)
    }

    @Test
    fun `does not ask for the persona while the server is down`() = runTest(mainDispatcher.dispatcher) {
        server.status = ConnectionStatus.Unauthorised
        val vm = viewModel()
        backgroundScope.launch { vm.status.collect {} }

        assertEquals(0, server.personaAsked)
    }
}
