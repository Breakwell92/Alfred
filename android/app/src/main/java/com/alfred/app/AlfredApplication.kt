package com.alfred.app

import android.app.Application
import com.alfred.app.data.reliability.ReliabilityMonitor
import com.alfred.app.di.appModule
import org.koin.android.ext.android.get
import org.koin.android.ext.koin.androidContext
import org.koin.core.context.startKoin

class AlfredApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        startKoin {
            androidContext(this@AlfredApplication)
            modules(appModule)
        }
        // Network-change reconnects and the reconnect count (ADR 0025), for the app's lifetime.
        get<ReliabilityMonitor>().start()
    }
}
