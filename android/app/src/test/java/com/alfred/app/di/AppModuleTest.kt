package com.alfred.app.di

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import com.alfred.app.ui.home.HomeViewModel
import com.alfred.app.ui.settings.SettingsViewModel
import org.junit.Rule
import org.junit.Test
import org.koin.dsl.koinApplication
import org.koin.dsl.module

class AppModuleTest {

    @get:Rule
    val mainDispatcher = MainDispatcherRule()

    /** Resolves the ViewModels from the real module, so a broken definition fails here, not at launch. */
    @Test
    fun `app module builds the view models`() {
        // The DataStore repository needs an Android Context and the Keystore a device; swap
        // both for fakes.
        val noAndroid = module {
            single<SettingsRepository> { FakeSettingsRepository(AppSettings("http://glados:8000", "")) }
            single<DeviceKeys> { FakeDeviceKeys() }
        }
        val koin = koinApplication {
            allowOverride(true)
            modules(appModule, noAndroid)
        }.koin

        koin.get<HomeViewModel>()
        koin.get<SettingsViewModel>()
        koin.get<ApprovalSigner>()
    }
}
