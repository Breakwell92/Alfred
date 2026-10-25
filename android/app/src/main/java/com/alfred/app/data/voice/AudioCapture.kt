package com.alfred.app.data.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.NoiseSuppressor
import androidx.core.content.ContextCompat
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.isActive

/** Bytes in one 20 ms frame of 16 kHz mono PCM16. */
const val FRAME_BYTES = INPUT_SAMPLE_RATE / 50 * 2

/** The microphone, as a stream of 20 ms PCM16 frames at 16 kHz. */
fun interface AudioCapture {
    /**
     * Records while collected and stops when the collector is cancelled. Throws
     * [SecurityException] without `RECORD_AUDIO`, [IllegalStateException] if the mic can't open.
     */
    fun frames(): Flow<ByteArray>
}

class AudioRecordCapture(private val context: Context) : AudioCapture {

    private val sessions = ConcurrentHashMap.newKeySet<Int>()

    /** Audio session ids of our own open recordings, so other apps' can be told apart. */
    val activeSessionIds: Set<Int> get() = sessions

    override fun frames(): Flow<ByteArray> = flow {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) throw SecurityException("Microphone permission not granted")
        val minBuffer = AudioRecord.getMinBufferSize(INPUT_SAMPLE_RATE, CHANNEL, ENCODING)
        val record = AudioRecord(
            // Tuned for speech recognition: no AGC on most devices, which STT prefers.
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            INPUT_SAMPLE_RATE,
            CHANNEL,
            ENCODING,
            maxOf(minBuffer, FRAME_BYTES * 10),
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IllegalStateException("Couldn't open the microphone")
        }
        val sessionId = record.audioSessionId
        sessions += sessionId
        val effects = attachEffects(sessionId)
        try {
            record.startRecording()
            val buffer = ByteArray(FRAME_BYTES)
            while (currentCoroutineContext().isActive) {
                var filled = 0
                while (filled < FRAME_BYTES) {
                    val read = record.read(buffer, filled, FRAME_BYTES - filled)
                    if (read < 0) throw IllegalStateException("Microphone read failed ($read)")
                    filled += read
                }
                emit(buffer.copyOf())
            }
        } finally {
            effects.forEach(AudioEffect::release)
            if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
            record.release()
            sessions -= sessionId
        }
    }.flowOn(Dispatchers.IO)

    /** Noise suppression and echo cancellation where the phone has them. */
    private fun attachEffects(sessionId: Int): List<AudioEffect> = buildList {
        if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(sessionId)?.let { it.enabled = true; add(it) }
        if (AcousticEchoCanceler.isAvailable()) {
            AcousticEchoCanceler.create(sessionId)?.let { it.enabled = true; add(it) }
        }
    }

    private companion object {
        const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
    }
}
