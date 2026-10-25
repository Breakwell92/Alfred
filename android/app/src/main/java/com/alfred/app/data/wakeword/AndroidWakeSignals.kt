package com.alfred.app.data.wakeword

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.media.AudioRecordingConfiguration
import android.media.ToneGenerator
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flow

/** [WakeSignals] from the system: recording callbacks, the audio mode and the DND filter. */
class AndroidWakeSignals(
    private val context: Context,
    /** Our own open recordings (audio session ids), which don't count as "another app". */
    private val ownSessions: () -> Set<Int>,
) : WakeSignals {
    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val notificationManager = context.getSystemService(NotificationManager::class.java)

    override val otherAppRecording: Flow<Boolean> = callbackFlow {
        fun check(configs: List<AudioRecordingConfiguration>) =
            configs.any { it.clientAudioSessionId !in ownSessions() }

        val callback = object : AudioManager.AudioRecordingCallback() {
            override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
                trySend(check(configs))
            }
        }
        audioManager.registerAudioRecordingCallback(callback, Handler(Looper.getMainLooper()))
        trySend(check(audioManager.activeRecordingConfigurations))
        awaitClose { audioManager.unregisterAudioRecordingCallback(callback) }
    }.distinctUntilChanged()

    override val inCall: Flow<Boolean> = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        callbackFlow {
            val listener = AudioManager.OnModeChangedListener { mode -> trySend(isCallMode(mode)) }
            audioManager.addOnModeChangedListener(ContextCompat.getMainExecutor(context), listener)
            trySend(isCallMode(audioManager.mode))
            awaitClose { audioManager.removeOnModeChangedListener(listener) }
        }.distinctUntilChanged()
    } else {
        // No mode listener before Android 12: check every few seconds.
        flow {
            while (true) {
                emit(isCallMode(audioManager.mode))
                delay(CALL_POLL_MILLIS)
            }
        }.distinctUntilChanged()
    }

    override val doNotDisturb: Flow<Boolean> = callbackFlow {
        fun current() = notificationManager.currentInterruptionFilter.let {
            it != NotificationManager.INTERRUPTION_FILTER_ALL && it != NotificationManager.INTERRUPTION_FILTER_UNKNOWN
        }

        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                trySend(current())
            }
        }
        ContextCompat.registerReceiver(
            context,
            receiver,
            IntentFilter(NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        trySend(current())
        awaitClose { context.unregisterReceiver(receiver) }
    }.distinctUntilChanged()

    private fun isCallMode(mode: Int) =
        mode == AudioManager.MODE_IN_CALL || mode == AudioManager.MODE_IN_COMMUNICATION ||
            mode == AudioManager.MODE_RINGTONE

    private companion object {
        const val CALL_POLL_MILLIS = 3_000L
    }
}

/** A short beep (until personas bring their own chime) and a light tick of vibration. */
class AndroidWakeFeedback(private val context: Context) : WakeFeedback {

    override suspend fun onWake(chime: Boolean) {
        vibrator()?.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
        if (!chime) return
        val tone = runCatching { ToneGenerator(AudioManager.STREAM_NOTIFICATION, CHIME_VOLUME) }.getOrNull() ?: return
        try {
            tone.startTone(ToneGenerator.TONE_PROP_BEEP, CHIME_MILLIS)
            // Let it finish before the mic opens, so the beep isn't sent as part of the request.
            delay(CHIME_MILLIS + 50L)
        } finally {
            tone.release()
        }
    }

    private fun vibrator(): Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        context.getSystemService(VibratorManager::class.java)?.defaultVibrator
    } else {
        context.getSystemService(Vibrator::class.java)
    }

    private companion object {
        const val CHIME_VOLUME = 80
        const val CHIME_MILLIS = 150
    }
}
