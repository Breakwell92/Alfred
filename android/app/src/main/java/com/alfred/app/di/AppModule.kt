package com.alfred.app.di

import com.alfred.app.BuildConfig
import com.alfred.app.data.device.AndroidKeyStoreDeviceKeys
import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.server.OkHttpServerClient
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.DataStoreSettingsRepository
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.settings.settingsDataStore
import com.alfred.app.data.voice.AudioCapture
import com.alfred.app.data.voice.AudioPlayer
import com.alfred.app.data.voice.AudioRecordCapture
import com.alfred.app.data.voice.AudioTrackPlayer
import com.alfred.app.data.voice.DataStoreDeviceIdRepository
import com.alfred.app.data.voice.DeviceConnection
import com.alfred.app.data.voice.DeviceIdRepository
import com.alfred.app.data.voice.OkHttpDeviceConnection
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.ui.home.HomeViewModel
import com.alfred.app.ui.home.VoiceViewModel
import com.alfred.app.ui.settings.SettingsViewModel
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient
import org.koin.android.ext.koin.androidContext
import org.koin.core.module.dsl.viewModel
import org.koin.core.qualifier.named
import org.koin.dsl.module

val appModule = module {
    single {
        OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .build()
    }
    single<SettingsRepository> {
        DataStoreSettingsRepository(androidContext().settingsDataStore, BuildConfig.DEFAULT_SERVER_URL)
    }
    single<ServerClient> { OkHttpServerClient(get()) }
    single<DeviceKeys> { AndroidKeyStoreDeviceKeys() }
    // Signs app_ui approvals; the confirmation screen uses it.
    single { ApprovalSigner(get()) }
    // Lives as long as the app: the voice connection and turns outlive any one screen.
    single(named(APP_SCOPE)) { CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate) }
    single<DeviceIdRepository> { DataStoreDeviceIdRepository(androidContext().settingsDataStore) }
    single<DeviceConnection> {
        OkHttpDeviceConnection(get(), get(), get(), BuildConfig.VERSION_NAME, get(named(APP_SCOPE)))
    }
    single<AudioCapture> { AudioRecordCapture(androidContext()) }
    single<AudioPlayer> { AudioTrackPlayer(androidContext()) }
    // Explicit so Koin doesn't try to inject the defaulted dispatcher and clock.
    single { VoiceController(get(), get(), get(), get(), get(), get(), get(named(APP_SCOPE))) }
    viewModel { VoiceViewModel(get(), get()) }
    // Explicit so Koin doesn't try to inject HomeViewModel's poll interval (a defaulted Long).
    viewModel { HomeViewModel(get(), get()) }
    // Explicit so Koin doesn't try to inject the Keystore dispatcher (a defaulted parameter).
    viewModel { SettingsViewModel(get(), get(), get()) }
}

const val APP_SCOPE = "appScope"
