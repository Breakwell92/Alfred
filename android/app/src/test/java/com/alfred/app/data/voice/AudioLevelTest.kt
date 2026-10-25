package com.alfred.app.data.voice

import kotlin.math.PI
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Assert.assertEquals
import org.junit.Test

class AudioLevelTest {

    @Test
    fun `rms of known samples`() {
        assertEquals(1_000.0, pcm16Rms(pcm(1_000, -1_000, 1_000, -1_000)), 1e-9)
        assertEquals(sqrt(12.5), pcm16Rms(pcm(3, 4, -4, 3)), 1e-9)
        assertEquals(32_768.0, pcm16Rms(pcm(Short.MIN_VALUE.toInt())), 1e-9)
    }

    @Test
    fun `rms reads only the given range`() {
        val bytes = pcm(30_000, 100, -100, 30_000)
        assertEquals(100.0, pcm16Rms(bytes, offset = 2, length = 4), 1e-9)
    }

    @Test
    fun `no whole sample is silence`() {
        assertEquals(0.0, pcm16Rms(ByteArray(0)), 0.0)
        assertEquals(0.0, pcm16Rms(byteArrayOf(1)), 0.0)
        assertEquals(0f, pcm16Level(byteArrayOf(1)), 0f)
    }

    @Test
    fun `dBFS is relative to full scale`() {
        assertEquals(0f, rmsToDbfs(32_768.0), 1e-4f)
        assertEquals(-6.0206f, rmsToDbfs(16_384.0), 1e-3f)
        assertEquals(Float.NEGATIVE_INFINITY, rmsToDbfs(0.0), 0f)
    }

    @Test
    fun `level maps the floor to the ceiling onto 0 to 1`() {
        assertEquals(0f, dbfsToLevel(LEVEL_FLOOR_DBFS), 0f)
        assertEquals(1f, dbfsToLevel(LEVEL_CEILING_DBFS), 0f)
        assertEquals(0.5f, dbfsToLevel(-30f), 1e-6f)
        assertEquals(0f, dbfsToLevel(-80f), 0f)
        assertEquals(1f, dbfsToLevel(0f), 0f)
        assertEquals(0f, dbfsToLevel(Float.NEGATIVE_INFINITY), 0f)
    }

    @Test
    fun `level of real buffers`() {
        assertEquals(0f, pcm16Level(ByteArray(FRAME_BYTES)), 0f)
        // A sine's RMS is its peak / √2, so a peak of √2 × 10^(-30/20) full scale is -30 dBFS.
        val peak = 2.0.pow(0.5) * 10.0.pow(-30.0 / 20) * 32_768
        assertEquals(0.5f, pcm16Level(sine(peak)), 0.01f)
        assertEquals(1f, pcm16Level(sine(30_000.0)), 0f)
        assertEquals(0f, pcm16Level(sine(50.0)), 0f)
    }

    /** One 20 ms frame of a sine (500 Hz is a whole number of cycles in it). */
    private fun sine(peak: Double, hz: Double = 500.0): ByteArray {
        val samples = IntArray(FRAME_BYTES / 2) { i ->
            (peak * sin(2 * PI * hz * i / INPUT_SAMPLE_RATE)).roundToInt()
        }
        return pcm(*samples)
    }
}

/** Little-endian PCM16 from sample values. */
internal fun pcm(vararg samples: Int): ByteArray {
    val bytes = ByteArray(samples.size * 2)
    samples.forEachIndexed { i, v ->
        bytes[2 * i] = (v and 0xFF).toByte()
        bytes[2 * i + 1] = (v shr 8 and 0xFF).toByte()
    }
    return bytes
}
