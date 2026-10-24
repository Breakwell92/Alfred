package com.alfred.app.ui.settings

import com.alfred.app.data.settings.AppSettings
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SettingsViewModelTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    private val repository =
        FakeSettingsRepository(AppSettings(serverUrl = "http://glados:8000", apiToken = ""))

    @Test
    fun `loads the stored settings`() = runTest(mainDispatcher.dispatcher) {
        val vm = SettingsViewModel(repository)

        val state = vm.state.value
        assertTrue(state.loaded)
        assertEquals("http://glados:8000", state.serverUrl)
        assertEquals("", state.apiToken)
    }

    @Test
    fun `save trims whitespace and trailing slashes`() = runTest(mainDispatcher.dispatcher) {
        val vm = SettingsViewModel(repository)
        vm.onServerUrlChange("  http://100.64.0.1:8000/ ")
        vm.onApiTokenChange(" secret \n")

        vm.save()

        assertEquals(
            AppSettings(serverUrl = "http://100.64.0.1:8000", apiToken = "secret"),
            repository.settings.first(),
        )
        assertTrue(vm.state.value.saved)
    }

    @Test
    fun `token with non-header characters is not saved`() = runTest(mainDispatcher.dispatcher) {
        val vm = SettingsViewModel(repository)
        vm.onApiTokenChange("tok’en")

        vm.save()

        assertEquals("", repository.settings.first().apiToken)
        assertFalse(vm.state.value.saved)
        assertNotNull(vm.state.value.tokenError)

        vm.onApiTokenChange("token")
        assertNull(vm.state.value.tokenError)
    }

    @Test
    fun `editing after saving clears the saved flag`() = runTest(mainDispatcher.dispatcher) {
        val vm = SettingsViewModel(repository)
        vm.save()

        vm.onApiTokenChange("other")

        assertFalse(vm.state.value.saved)
    }
}
