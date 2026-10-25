package com.alfred.app.data.voice

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTimestamp
import android.media.AudioTrack
import android.os.Build
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Streams one spoken reply at a time: [begin], any number of [write]s, then [finish] or [stop]. */
interface AudioPlayer {
    /** True from [begin] until the audio has played out or [stop] is called. */
    val playing: StateFlow<Boolean>

    /**
     * Loudness of the reply as it's heard, 0 to 1 (see [pcm16Level]), updated every 20 ms while
     * playing; 0 otherwise. Follows the presented position, so it lags [write] by the track's
     * buffer and output latency and moves with the sound.
     */
    val level: StateFlow<Float>

    /**
     * How much of the reply has been heard, in ms from its first sample (what `reply.words`
     * timings count from). Polled with [level], so it follows the sound; 0 when not playing.
     */
    val position: StateFlow<Long>

    /** Starts a new reply (stopping any current one). PCM16 mono at [sampleRate]. */
    fun begin(sampleRate: Int)

    fun write(pcm: ByteArray)

    /** No more audio for this reply: play out what's buffered, then release. */
    fun finish()

    /** Stops straight away and drops anything buffered. */
    fun stop()
}

/**
 * [AudioPlayer] on a streaming [AudioTrack]. All track work runs on one background thread, so
 * blocking writes never hold up the socket or the UI. Playback starts once [JITTER_MILLIS] of
 * audio is buffered (or on [finish]), which smooths out network jitter between frames. Other
 * audio is ducked while Alfred speaks, and a connected Bluetooth output is preferred. [level] is
 * polled on its own thread from the track's presentation timestamp.
 */
class AudioTrackPlayer(context: Context) : AudioPlayer {

    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "alfred-audio-out").apply { isDaemon = true }
    }

    /** Bumped by [begin] and [stop]; queued work for an older reply is skipped. */
    private val generation = AtomicInteger()

    private val poller: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor { runnable ->
        Thread(runnable, "alfred-audio-level").apply { isDaemon = true }
    }

    private val _playing = MutableStateFlow(false)
    override val playing: StateFlow<Boolean> = _playing.asStateFlow()

    private val _level = MutableStateFlow(0f)
    override val level: StateFlow<Float> = _level.asStateFlow()

    private val _position = MutableStateFlow(0L)
    override val position: StateFlow<Long> = _position.asStateFlow()

    // Touched only on the executor thread, except [track], which [stop] pauses directly to
    // unblock a pending write, and the volatile ones the level poller reads.
    @Volatile
    private var track: AudioTrack? = null
    private var focus: AudioFocusRequest? = null
    private val pending = ByteArrayOutputStream()

    @Volatile
    private var started = false

    @Volatile
    private var levels = PlaybackLevels(OUTPUT_SAMPLE_RATE * LEVEL_WINDOW_MILLIS / 1000)

    @Volatile
    private var levelPoll: ScheduledFuture<*>? = null

    @Volatile
    private var sampleRate = OUTPUT_SAMPLE_RATE

    /** Reused by every poll; only the poller thread touches it. */
    private val timestamp = AudioTimestamp()

    /** PCM written to the track for this reply; the poller caps the heard position at it. */
    @Volatile
    private var bytesWritten = 0L
    private var bytesPerSecond = OUTPUT_SAMPLE_RATE * 2

    override fun begin(sampleRate: Int) {
        val gen = generation.incrementAndGet()
        _playing.value = true
        executor.execute {
            release()
            if (gen != generation.get()) return@execute
            open(sampleRate)
        }
    }

    override fun write(pcm: ByteArray) {
        val gen = generation.get()
        executor.execute {
            if (gen != generation.get() || track == null) return@execute
            if (started) {
                writeFully(pcm, gen)
            } else {
                pending.write(pcm)
                if (pending.size() >= bytesPerSecond * JITTER_MILLIS / 1000) startPlaying(gen)
            }
        }
    }

    override fun finish() {
        val gen = generation.get()
        executor.execute {
            if (gen != generation.get()) return@execute
            if (track != null && !started) startPlaying(gen)
            drain(gen)
            if (gen == generation.get()) {
                release()
                _playing.value = false
            }
        }
    }

    override fun stop() {
        generation.incrementAndGet()
        track?.runCatching {
            pause()
            flush()
        }
        stopLevels()
        _playing.value = false
        executor.execute { release() }
    }

    private fun open(sampleRate: Int) {
        bytesPerSecond = sampleRate * 2
        val attributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val newTrack = AudioTrack.Builder()
            .setAudioAttributes(attributes)
            .setAudioFormat(format)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(minBuffer, bytesPerSecond / 2))
            .build()
        bluetoothOutput()?.let(newTrack::setPreferredDevice)
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(attributes)
            .build()
        audioManager.requestAudioFocus(request)
        focus = request
        track = newTrack
        pending.reset()
        started = false
        bytesWritten = 0
        this.sampleRate = sampleRate
        levels = PlaybackLevels(sampleRate * LEVEL_WINDOW_MILLIS / 1000)
        levelPoll = poller.scheduleWithFixedDelay(
            ::pollLevel,
            0,
            LEVEL_WINDOW_MILLIS.toLong(),
            TimeUnit.MILLISECONDS,
        )
    }

    /** Publishes the level and position of the audio being heard now (0 until playback starts). */
    private fun pollLevel() {
        val current = track
        val heard = if (current != null && started) current.runCatching(::heardFrame).getOrNull() else null
        _level.value = heard?.let(levels::levelAt) ?: 0f
        _position.value = heard?.let { heardMillis(it, bytesWritten / 2, sampleRate) } ?: 0L
    }

    /**
     * The frame leaving the speaker now. [AudioTrack.getTimestamp] includes the output latency
     * after the mixer (100–250 ms over Bluetooth); it has nothing for a moment after `play()`,
     * and then the playback head (which leads what's heard by that latency) stands in.
     */
    private fun heardFrame(track: AudioTrack): Long =
        if (track.getTimestamp(timestamp)) {
            presentedFrame(timestamp.framePosition, timestamp.nanoTime, System.nanoTime(), sampleRate)
        } else {
            track.playbackHeadPosition.toLong() and 0xFFFFFFFFL
        }

    private fun stopLevels() {
        levelPoll?.cancel(false)
        levelPoll = null
        _level.value = 0f
        _position.value = 0L
        // A poll already running when cancelled could publish after the lines above; this runs
        // behind it on the same thread.
        poller.execute {
            if (levelPoll == null) {
                _level.value = 0f
                _position.value = 0L
            }
        }
    }

    private fun startPlaying(gen: Int) {
        val current = track ?: return
        current.play()
        started = true
        val buffered = pending.toByteArray()
        pending.reset()
        writeFully(buffered, gen)
    }

    private fun writeFully(pcm: ByteArray, gen: Int) {
        val current = track ?: return
        levels.add(pcm)
        var offset = 0
        while (offset < pcm.size && gen == generation.get()) {
            val written = current.write(pcm, offset, pcm.size - offset)
            if (written <= 0) return
            offset += written
            // Per piece, so the position cap (read by the poller) follows a blocking write.
            bytesWritten += written
        }
    }

    /** Waits until everything written has been played (or the reply was stopped). */
    private fun drain(gen: Int) {
        val current = track ?: return
        val frames = bytesWritten / 2
        val deadline = System.currentTimeMillis() + frames * 1000 / (bytesPerSecond / 2) + DRAIN_SLACK_MILLIS
        while (gen == generation.get() && System.currentTimeMillis() < deadline) {
            val played = current.playbackHeadPosition.toLong() and 0xFFFFFFFFL
            if (played >= frames) break
            Thread.sleep(DRAIN_POLL_MILLIS)
        }
    }

    private fun release() {
        stopLevels()
        track?.runCatching {
            stop()
            release()
        }
        track = null
        focus?.let(audioManager::abandonAudioFocusRequest)
        focus = null
        pending.reset()
        started = false
    }

    /** A connected Bluetooth speaker or headset, if any (else the system's default route). */
    private fun bluetoothOutput(): AudioDeviceInfo? {
        val types = buildSet {
            add(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(AudioDeviceInfo.TYPE_BLE_HEADSET)
                add(AudioDeviceInfo.TYPE_BLE_SPEAKER)
            }
        }
        return audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull { it.type in types }
    }

    private companion object {
        const val JITTER_MILLIS = 150
        const val LEVEL_WINDOW_MILLIS = 20
        const val DRAIN_POLL_MILLIS = 20L
        const val DRAIN_SLACK_MILLIS = 1_000L
    }
}
