package com.alfred.app.di

import com.alfred.app.BuildConfig
import com.alfred.app.data.device.AndroidKeyStoreDeviceKeys
import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.server.OkHttpServerClient
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.DataStoreProfileSettingsRepository
import com.alfred.app.data.settings.DataStoreSettingsRepository
import com.alfred.app.data.settings.ProfileSettingsRepository
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.settings.settingsDataStore
import com.alfred.app.data.voice.AudioCapture
import com.alfred.app.data.voice.AudioPlayer
import com.alfred.app.data.voice.AudioRecordCapture
import com.alfred.app.data.voice.AudioTrackPlayer
import com.alfred.app.data.voice.DataStoreDeviceIdRepository
import com.alfred.app.data.voice.DeviceConnection
import com.alfred.app.data.voice.DeviceIdRepository
import com.alfred.app.data.voice.LinkHolds
import com.alfred.app.data.voice.OkHttpDeviceConnection
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.wakeword.AndroidWakeFeedback
import com.alfred.app.data.wakeword.AndroidWakeSignals
import com.alfred.app.data.wakeword.DataStoreWakeWordSettingsRepository
import com.alfred.app.data.wakeword.FileWakeEventLog
import com.alfred.app.data.wakeword.OnnxWakeWordModels
import com.alfred.app.data.wakeword.WakeEventLog
import com.alfred.app.data.wakeword.WakeFeedback
import com.alfred.app.data.wakeword.WakeSignals
import com.alfred.app.data.wakeword.WakeWordController
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import com.alfred.app.ui.home.HomeViewModel
import com.alfred.app.ui.home.VoiceViewModel
import com.alfred.app.ui.recording.RecordingViewModel
import com.alfred.app.ui.settings.SettingsViewModel
import com.alfred.app.ui.settings.WakeWordSettingsViewModel
import com.alfred.app.ui.transmitting.TransmittingViewModel
import java.io.File
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
    single<ProfileSettingsRepository> { DataStoreProfileSettingsRepository(androidContext().settingsDataStore) }
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
    single { LinkHolds(get()) }
    single<AudioPlayer> { AudioTrackPlayer(androidContext()) }
    // Explicit so Koin doesn't try to inject the defaulted dispatcher and clock.
    single { VoiceController(get(), get(), get(), get(), get(), get(), get(named(APP_SCOPE))) }
    // Wake word (ADR 0019).
    single<WakeWordSettingsRepository> { DataStoreWakeWordSettingsRepository(androidContext().settingsDataStore) }
    single<WakeSignals> {
        val capture = get<AudioCapture>()
        AndroidWakeSignals(androidContext()) { (capture as? AudioRecordCapture)?.activeSessionIds.orEmpty() }
    }
    single<WakeFeedback> { AndroidWakeFeedback(androidContext()) }
    single<WakeEventLog> { FileWakeEventLog(File(androidContext().filesDir, "wake_events.jsonl")) }
    single {
        WakeWordController(
            settingsRepository = get(),
            voice = get(),
            capture = get(),
            signals = get(),
            feedback = get(),
            events = get(),
            loadModels = { model ->
                val assets = androidContext().assets
                OnnxWakeWordModels({ name -> assets.open("wakeword/$name").use { it.readBytes() } }, model.modelFile)
            },
            scope = get(named(APP_SCOPE)),
        )
    }
    viewModel { WakeWordSettingsViewModel(get(), get(), get()) }
    viewModel { VoiceViewModel(get(), get()) }
    viewModel { RecordingViewModel(get()) }
    // Explicit so Koin doesn't try to inject the defaulted detector factory and hold time.
    viewModel { TransmittingViewModel(get()) }
    // Explicit so Koin doesn't try to inject HomeViewModel's poll interval (a defaulted Long).
    viewModel { HomeViewModel(get(), get(), get()) }
    // Explicit so Koin doesn't try to inject the Keystore dispatcher (a defaulted parameter).
    viewModel { SettingsViewModel(get(), get(), get(), get()) }
}

const val APP_SCOPE = "appScope"
