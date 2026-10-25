package com.alfred.app.data.voice

import kotlin.math.log10
import kotlin.math.sqrt

/** Quietest level the orb shows: room noise and breaths sit below this. */
const val LEVEL_FLOOR_DBFS = -50f

/** Loudest level the orb shows: close, normal speech reaches this. */
const val LEVEL_CEILING_DBFS = -10f

private const val FULL_SCALE = 32_768.0

/**
 * RMS of little-endian PCM16 samples in `pcm[offset until offset + length]`, in sample units
 * (0 to 32 768). 0 when there's no whole sample.
 */
fun pcm16Rms(pcm: ByteArray, offset: Int = 0, length: Int = pcm.size - offset): Double {
    val samples = length / 2
    if (samples == 0) return 0.0
    var sum = 0.0
    for (i in 0 until samples) {
        val at = offset + 2 * i
        val v = ((pcm[at].toInt() and 0xFF) or (pcm[at + 1].toInt() shl 8)).toShort().toDouble()
        sum += v * v
    }
    return sqrt(sum / samples)
}

/** [rms] in dBFS (0 is full scale); negative infinity for digital silence. */
fun rmsToDbfs(rms: Double): Float =
    if (rms <= 0.0) Float.NEGATIVE_INFINITY else (20 * log10(rms / FULL_SCALE)).toFloat()

/** [dbfs] mapped linearly from [LEVEL_FLOOR_DBFS]..[LEVEL_CEILING_DBFS] onto 0..1, clamped. */
fun dbfsToLevel(dbfs: Float): Float =
    ((dbfs - LEVEL_FLOOR_DBFS) / (LEVEL_CEILING_DBFS - LEVEL_FLOOR_DBFS)).coerceIn(0f, 1f)

/** The 0–1 level of a PCM16 buffer, as `AlfredOrb` takes it. */
fun pcm16Level(pcm: ByteArray, offset: Int = 0, length: Int = pcm.size - offset): Float =
    dbfsToLevel(rmsToDbfs(pcm16Rms(pcm, offset, length)))

/**
 * The frame being presented at [nowNanos], extrapolated from an `AudioTimestamp` that saw
 * [framePosition] presented at [timestampNanos] (both on the `System.nanoTime` clock).
 */
fun presentedFrame(framePosition: Long, timestampNanos: Long, nowNanos: Long, sampleRate: Int): Long =
    (framePosition + (nowNanos - timestampNanos) * sampleRate / 1_000_000_000L).coerceAtLeast(0)

/**
 * Ms of the reply heard, from the [presentedFrame] estimate but never past the [framesWritten]
 * to the track: during an underrun the timestamp stops while the clock runs on, and the
 * extrapolation would otherwise run ahead into audio that hasn't arrived.
 */
fun heardMillis(presentedFrame: Long, framesWritten: Long, sampleRate: Int): Long =
    presentedFrame.coerceIn(0, framesWritten.coerceAtLeast(0)) * 1000 / sampleRate

/**
 * Levels of the PCM written to an output track, looked up by playback position: the level is
 * that of what's leaving the speaker now, not of what was just written (which can be up to the
 * track's buffer ahead). One level per [windowFrames] samples; a trailing part-window isn't
 * scored until it fills. Thread-safe: written from the audio thread, read from a poller.
 */
class PlaybackLevels(private val windowFrames: Int) {
    private val levels = ArrayDeque<Float>()

    /** Window index of `levels.first()`. */
    private var firstWindow = 0L
    private val partial = ByteArray(windowFrames * 2)
    private var partialBytes = 0

    @Synchronized
    fun add(pcm: ByteArray) {
        var offset = 0
        while (offset < pcm.size) {
            val take = minOf(partial.size - partialBytes, pcm.size - offset)
            pcm.copyInto(partial, partialBytes, offset, offset + take)
            partialBytes += take
            offset += take
            if (partialBytes == partial.size) {
                levels.addLast(pcm16Level(partial))
                partialBytes = 0
            }
        }
    }

    /** The level at [playedFrames] samples into the reply; 0 before or past the audio. */
    @Synchronized
    fun levelAt(playedFrames: Long): Float {
        val window = playedFrames / windowFrames
        while (firstWindow < window && levels.isNotEmpty()) {
            levels.removeFirst()
            firstWindow++
        }
        return if (window == firstWindow) levels.firstOrNull() ?: 0f else 0f
    }

    @Synchronized
    fun reset() {
        levels.clear()
        firstWindow = 0
        partialBytes = 0
    }
}
