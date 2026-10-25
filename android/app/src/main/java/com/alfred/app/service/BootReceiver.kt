package com.alfred.app.service

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.alfred.app.data.reliability.ReliabilityRecorder
import com.alfred.app.data.reliability.ServiceStarter
import com.alfred.app.data.reliability.StartReason
import com.alfred.app.data.reliability.Watchdog
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * After a reboot or an app update, puts the wake word back if it's on (ADR 0025). Android 14+
 * won't let a microphone service start from here, so on those phones this ends with the
 * "Tap to resume" notification instead; the watchdog is scheduled either way.
 */
class BootReceiver :
    BroadcastReceiver(),
    KoinComponent {
    private val settings: WakeWordSettingsRepository by inject()
    private val starter: ServiceStarter by inject()
    private val recorder: ReliabilityRecorder by inject()

    override fun onReceive(context: Context, intent: Intent) {
        val reason = when (intent.action) {
            // Not LOCKED_BOOT_COMPLETED: the settings DataStore can't be read before first unlock.
            Intent.ACTION_BOOT_COMPLETED -> StartReason.Boot
            Intent.ACTION_MY_PACKAGE_REPLACED -> StartReason.PackageReplaced
            else -> return
        }
        val pending = goAsync()
        CoroutineScope(Dispatchers.Main.immediate).launch {
            try {
                val hasMic = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                    PackageManager.PERMISSION_GRANTED
                if (settings.settings.first().enabled && hasMic && !starter.isRunning) {
                    Watchdog.schedule(context)
                    recorder.down(reason.wire)
                    starter.tryStart(reason)
                }
            } finally {
                pending.finish()
            }
        }
    }
}
