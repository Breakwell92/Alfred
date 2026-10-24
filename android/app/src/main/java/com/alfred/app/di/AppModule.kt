package com.alfred.app.di

import com.alfred.app.BuildConfig
import com.alfred.app.data.server.OkHttpServerClient
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.DataStoreSettingsRepository
import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.settings.settingsDataStore
import com.alfred.app.ui.home.HomeViewModel
import com.alfred.app.ui.settings.SettingsViewModel
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import org.koin.android.ext.koin.androidContext
import org.koin.core.module.dsl.viewModel
import org.koin.core.module.dsl.viewModelOf
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
    // Explicit so Koin doesn't try to inject HomeViewModel's poll interval (a defaulted Long).
    viewModel { HomeViewModel(get(), get()) }
    viewModelOf(::SettingsViewModel)
}
