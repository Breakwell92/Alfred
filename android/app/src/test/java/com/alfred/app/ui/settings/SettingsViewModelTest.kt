package com.alfred.app.ui.settings

import com.alfred.app.data.server.PairingResult
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeServerClient
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
    private val server = FakeServerClient()
    private val keys = FakeDeviceKeys()

    private fun viewModel() = SettingsViewModel(repository, server, keys, mainDispatcher.dispatcher)

    @Test
    fun `loads the stored settings`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()

        val state = vm.state.value
        assertTrue(state.loaded)
        assertEquals("http://glados:8000", state.serverUrl)
        assertEquals("", state.apiToken)
    }

    @Test
    fun `save trims whitespace and trailing slashes`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()
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
        val vm = viewModel()
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
        val vm = viewModel()
        vm.save()

        vm.onApiTokenChange("other")

        assertFalse(vm.state.value.saved)
    }

    @Test
    fun `pairing sends the public key and stores the device`() = runTest(mainDispatcher.dispatcher) {
        repository.save(AppSettings(serverUrl = "http://glados:8000", apiToken = "secret"))
        val vm = viewModel()
        vm.onPairingCodeChange(" abcd-efgh ")

        vm.pair()

        val (settings, code, publicKey) = server.paired.single()
        assertEquals("secret", settings.apiToken)
        assertEquals("abcd-efgh", code)
        assertTrue(publicKey.contentEquals(keys.keyPair.public.encoded))
        assertEquals("device-1", repository.settings.first().pairedDeviceId)
        assertEquals("pixel", vm.state.value.pairedDeviceName)
        assertEquals("", vm.state.value.pairingCode)
        assertFalse(vm.state.value.pairing)
    }

    @Test
    fun `a rejected code shows an error and keeps it`() = runTest(mainDispatcher.dispatcher) {
        server.pairing = PairingResult.InvalidCode
        val vm = viewModel()
        vm.onPairingCodeChange("AAAA-AAAA")

        vm.pair()

        assertEquals("That code is wrong, used or expired", vm.state.value.pairingError)
        assertEquals("AAAA-AAAA", vm.state.value.pairingCode)
        assertNull(repository.settings.first().pairedDeviceId)
        assertNull(vm.state.value.pairedDeviceName)
    }

    @Test
    fun `an empty code is not sent`() = runTest(mainDispatcher.dispatcher) {
        val vm = viewModel()

        vm.pair()

        assertTrue(server.paired.isEmpty())
        assertNotNull(vm.state.value.pairingError)
    }

    @Test
    fun `a keystore failure is shown`() = runTest(mainDispatcher.dispatcher) {
        keys.error = IllegalStateException("no keystore")
        val vm = viewModel()
        vm.onPairingCodeChange("ABCD-EFGH")

        vm.pair()

        assertTrue(server.paired.isEmpty())
        assertEquals("Couldn't create the approval key: no keystore", vm.state.value.pairingError)
    }

    @Test
    fun `saving keeps the pairing`() = runTest(mainDispatcher.dispatcher) {
        repository.savePairing("device-1", "pixel")
        val vm = viewModel()
        assertEquals("pixel", vm.state.value.pairedDeviceName)
        vm.onServerUrlChange("http://other:8000")

        vm.save()

        assertEquals("device-1", repository.settings.first().pairedDeviceId)
    }
}
