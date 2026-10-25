package com.alfred.app.di

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.ProfileSettingsRepository
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.voice.AudioCapture
import com.alfred.app.data.voice.AudioPlayer
import com.alfred.app.data.voice.DeviceConnection
import com.alfred.app.data.voice.DeviceIdRepository
import com.alfred.app.data.voice.LinkHolds
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.wakeword.WakeEventLog
import com.alfred.app.data.wakeword.WakeFeedback
import com.alfred.app.data.wakeword.WakeSignals
import com.alfred.app.data.wakeword.WakeWordController
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import com.alfred.app.testing.FakeAudioCapture
import com.alfred.app.testing.FakeAudioPlayer
import com.alfred.app.testing.FakeDeviceKeys
import com.alfred.app.testing.FakeProfileSettingsRepository
import com.alfred.app.testing.FakeSettingsRepository
import com.alfred.app.testing.FakeWakeEventLog
import com.alfred.app.testing.FakeWakeFeedback
import com.alfred.app.testing.FakeWakeSignals
import com.alfred.app.testing.FakeWakeWordSettingsRepository
import com.alfred.app.testing.MainDispatcherRule
import com.alfred.app.ui.home.HomeViewModel
import com.alfred.app.ui.home.VoiceViewModel
import com.alfred.app.ui.recording.RecordingViewModel
import com.alfred.app.ui.settings.SettingsViewModel
import com.alfred.app.ui.settings.WakeWordSettingsViewModel
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
        // The DataStore repositories need an Android Context, the Keystore a device and the
        // audio classes the platform; swap them for fakes.
        val noAndroid = module {
            single<SettingsRepository> { FakeSettingsRepository(AppSettings("http://glados:8000", "")) }
            single<DeviceKeys> { FakeDeviceKeys() }
            single<DeviceIdRepository> { DeviceIdRepository { "device-1" } }
            single<AudioCapture> { FakeAudioCapture() }
            single<AudioPlayer> { FakeAudioPlayer() }
            single<WakeWordSettingsRepository> { FakeWakeWordSettingsRepository() }
            single<ProfileSettingsRepository> { FakeProfileSettingsRepository() }
            single<WakeSignals> { FakeWakeSignals() }
            single<WakeFeedback> { FakeWakeFeedback() }
            single<WakeEventLog> { FakeWakeEventLog() }
        }
        val koin = koinApplication {
            allowOverride(true)
            modules(appModule, noAndroid)
        }.koin

        koin.get<HomeViewModel>()
        koin.get<SettingsViewModel>()
        koin.get<ApprovalSigner>()
        koin.get<DeviceConnection>()
        koin.get<VoiceController>()
        koin.get<VoiceViewModel>()
        koin.get<LinkHolds>()
        koin.get<WakeWordController>()
        koin.get<WakeWordSettingsViewModel>()
        koin.get<RecordingViewModel>()
    }
}
