package com.alfred.app.data.reliability

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.first
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/** Every 15 minutes (WorkManager's shortest period) while the wake word is on (ADR 0025). */
object Watchdog {
    private const val WORK_NAME = "wake_word_watchdog"

    fun schedule(context: Context) {
        val request = PeriodicWorkRequestBuilder<WatchdogWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(context)
            .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
    }
}

/** Restarts the wake word service if it should be running and isn't, and logs the gap. */
class WatchdogWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params),
    KoinComponent {
    private val settings: WakeWordSettingsRepository by inject()
    private val starter: ServiceStarter by inject()
    private val recorder: ReliabilityRecorder by inject()

    override suspend fun doWork(): Result {
        val enabled = settings.settings.first().enabled
        val hasMic = ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (watchdogAction(enabled, starter.isRunning, hasMic) == WatchdogAction.Restart) {
            recorder.down("watchdog")
            starter.tryStart(StartReason.Watchdog)
        }
        return Result.success()
    }
}
