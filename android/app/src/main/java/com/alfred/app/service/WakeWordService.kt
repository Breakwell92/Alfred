package com.alfred.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.alfred.app.MainActivity
import com.alfred.app.R
import com.alfred.app.data.voice.LinkHolds
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.wakeword.PauseReason
import com.alfred.app.data.wakeword.RecentWake
import com.alfred.app.data.wakeword.WakeState
import com.alfred.app.data.wakeword.WakeWordController
import com.alfred.app.data.wakeword.describe
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject

/**
 * Listens for the wake word in the background (ADR 0019): a `microphone` foreground service
 * that keeps the device socket open and runs the [WakeWordController]. Its notification shows
 * what it's doing, with Pause / Resume and, just after a wake, "That wasn't me". Started and
 * stopped by [MainActivity] to match the Settings toggle; it keeps running when the app leaves
 * the screen.
 */
class WakeWordService : Service() {
    private val controller: WakeWordController by inject()
    private val links: LinkHolds by inject()
    private val voice: VoiceController by inject()
    private val scope = MainScope()

    override fun onCreate() {
        super.onCreate()
        createChannel()
        if (!startInForeground(notification(WakeState.Off, null))) {
            stopSelf()
            return
        }
        links.hold(LinkHolds.WAKE_WORD)
        controller.start()
        scope.launch {
            combine(controller.state, controller.recentWake, ::Pair).collect { (state, wake) ->
                getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(state, wake))
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> controller.pause()
            ACTION_RESUME -> controller.resume()
            ACTION_NOT_ME -> controller.reportFalseAccept()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        controller.stop()
        // The app isn't on screen either: nothing else needs the turn or the socket.
        if (links.release(LinkHolds.WAKE_WORD)) voice.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /**
     * False if Android won't let us record in the background now (e.g. restarted while the app
     * isn't in the foreground, or the mic permission was revoked).
     */
    private fun startInForeground(notification: Notification): Boolean = try {
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            // The microphone type exists from Android 11; Android 10 needs no type for it.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0,
        )
        true
    } catch (e: RuntimeException) {
        Log.w(TAG, "Couldn't start the wake word service in the foreground", e)
        false
    }

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, "Wake word", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Shows while Alfred is listening for the wake word"
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun notification(state: WakeState, wake: RecentWake?): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_wake_word)
            .setContentTitle(title(state))
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        if (state is WakeState.Paused && state.reason == PauseReason.User) {
            builder.addAction(0, "Resume", action(ACTION_RESUME))
        } else {
            builder.addAction(0, "Pause", action(ACTION_PAUSE))
        }
        if (wake != null) builder.addAction(0, "That wasn't me", action(ACTION_NOT_ME))
        return builder.build()
    }

    private fun title(state: WakeState): String =
        if (state == WakeState.Off) "Starting the wake word listener" else state.describe()

    private fun action(name: String): PendingIntent = PendingIntent.getService(
        this,
        name.hashCode(),
        Intent(this, WakeWordService::class.java).setAction(name),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    companion object {
        private const val TAG = "WakeWordService"
        private const val CHANNEL_ID = "wake_word"
        private const val NOTIFICATION_ID = 68
        private const val ACTION_PAUSE = "com.alfred.app.wakeword.PAUSE"
        private const val ACTION_RESUME = "com.alfred.app.wakeword.RESUME"
        private const val ACTION_NOT_ME = "com.alfred.app.wakeword.NOT_ME"

        /** Call while the app is in the foreground (Android only allows starting mic services then). */
        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, WakeWordService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WakeWordService::class.java))
        }
    }
}
