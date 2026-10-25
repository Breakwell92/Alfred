package com.alfred.app.data.reliability

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import com.alfred.app.MainActivity
import com.alfred.app.R
import com.alfred.app.service.WakeWordService

/** Starts the wake word service from the background, or asks Dan to (ADR 0025). */
interface ServiceStarter {
    val isRunning: Boolean

    /** False if Android refused; the stopped-listening notification is up instead. */
    suspend fun tryStart(reason: StartReason): Boolean
}

class AndroidServiceStarter(
    private val context: Context,
    private val recorder: ReliabilityRecorder,
) : ServiceStarter {
    override val isRunning: Boolean
        get() = WakeWordService.isRunning

    override suspend fun tryStart(reason: StartReason): Boolean = try {
        WakeWordService.start(context, reason)
        true
    } catch (e: IllegalStateException) {
        // ForegroundServiceStartNotAllowedException (Android 12+) is an IllegalStateException.
        blocked(reason, e)
    } catch (e: SecurityException) {
        blocked(reason, e)
    }

    private suspend fun blocked(reason: StartReason, e: Exception): Boolean {
        Log.w(TAG, "Couldn't start the wake word service (${reason.wire})", e)
        recorder.restartBlocked(reason.wire)
        StoppedListeningNotification.show(context)
        return false
    }

    private companion object {
        const val TAG = "ServiceStarter"
    }
}

/**
 * "Alfred stopped listening · Tap to resume". Android won't let a microphone service start from
 * the background (boot, the watchdog, a restart after the process was killed), but it will from
 * an activity Dan opens: the tap opens [MainActivity], which starts it.
 */
object StoppedListeningNotification {
    private const val CHANNEL_ID = "wake_word_stopped"
    private const val NOTIFICATION_ID = 71

    fun show(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Wake word stopped", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "When Android stopped the wake word listener and it needs a tap to resume"
            },
        )
        val open = PendingIntent.getActivity(
            context,
            NOTIFICATION_ID,
            Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(WakeWordService.EXTRA_START_REASON, StartReason.Resume.wire),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_wake_word)
            .setContentTitle("Alfred stopped listening")
            .setContentText("Tap to resume the wake word")
            .setContentIntent(open)
            .setAutoCancel(true)
            // The watchdog re-posts it every 15 minutes; buzz only the first time.
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .build()
        // Without the notification permission this is silently dropped; nothing else to do.
        manager.notify(NOTIFICATION_ID, notification)
    }

    fun cancel(context: Context) {
        context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }
}
