package com.alfred.app.data.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EnergyEndOfSpeechTest {

    /** One 20 ms frame of a square wave at [level] (its RMS). */
    private fun frame(level: Int): ByteArray = ByteArray(640).also { out ->
        for (i in 0 until 320) {
            val v = if (i % 2 == 0) level else -level
            out[2 * i] = v.toByte()
            out[2 * i + 1] = (v shr 8).toByte()
        }
    }

    /** Feeds [millis] of frames at [level]; true if any frame ended the turn. */
    private fun EnergyEndOfSpeech.feed(millis: Int, level: Int): Boolean =
        (0 until millis / 20).map { onFrame(frame(level)) }.any { it != Endpoint.Continue }

    @Test
    fun `ends after a second of quiet following speech`() {
        val eos = EnergyEndOfSpeech()
        assertFalse(eos.feed(200, level = 50))
        assertFalse(eos.feed(1_500, level = 3_000))
        assertFalse(eos.feed(980, level = 50))
        assertEquals(Endpoint.End, eos.onFrame(frame(50)))
        // Stays ended.
        assertEquals(Endpoint.End, eos.onFrame(frame(3_000)))
    }

    @Test
    fun `short pauses between words don't end it`() {
        val eos = EnergyEndOfSpeech()
        repeat(5) {
            assertFalse(eos.feed(600, level = 3_000))
            assertFalse(eos.feed(500, level = 50))
        }
    }

    @Test
    fun `gives up when nobody speaks`() {
        val eos = EnergyEndOfSpeech()
        assertFalse(eos.feed(4_980, level = 50))
        assertEquals(Endpoint.NoSpeech, eos.onFrame(frame(50)))
    }

    @Test
    fun `steady background noise isn't speech`() {
        // A loud fan: well above the absolute minimum, but steady, so the noise floor follows it.
        val eos = EnergyEndOfSpeech(noSpeechMillis = 60_000, maxMillis = 60_000)
        assertFalse(eos.feed(2_000, level = 400))
        // Speech over the fan, then just the fan again.
        assertFalse(eos.feed(1_000, level = 4_000))
        assertTrue(eos.feed(1_100, level = 400))
    }

    @Test
    fun `stops at the cap`() {
        val eos = EnergyEndOfSpeech()
        assertFalse(eos.feed(400, level = 50))
        // Talking non-stop (short breaths only) for 15 s.
        var ended = false
        repeat(19) { ended = ended || eos.feed(600, level = 3_000) || eos.feed(200, level = 50) }
        assertTrue(ended)
        val fresh = EnergyEndOfSpeech()
        assertFalse(fresh.feed(400, level = 50))
        repeat(17) {
            assertFalse(fresh.feed(600, level = 3_000))
            assertFalse(fresh.feed(200, level = 50))
        }
    }

    @Test
    fun `speech from the first frame is noticed after the first breath`() {
        val eos = EnergyEndOfSpeech()
        assertFalse(eos.feed(800, level = 3_000))
        assertFalse(eos.feed(200, level = 50))
        assertFalse(eos.feed(800, level = 3_000))
        assertFalse(eos.feed(980, level = 50))
        assertEquals(Endpoint.End, eos.onFrame(frame(50)))
    }

    @Test
    fun `the chime's echo and the recorder starting don't count as speech`() {
        // What the phone sent: blank frames, the tail of the beep, then quiet while Dan waits.
        val eos = EnergyEndOfSpeech()
        assertFalse(eos.feed(60, level = 0))
        assertFalse(eos.feed(200, level = 4_000))
        assertFalse(eos.feed(1_500, level = 60))
        // Then he speaks, and the turn ends a second after he stops.
        assertFalse(eos.feed(1_200, level = 2_500))
        assertFalse(eos.feed(980, level = 60))
        assertEquals(Endpoint.End, eos.onFrame(frame(60)))
    }

    @Test
    fun `a click isn't the start of speech`() {
        val eos = EnergyEndOfSpeech()
        assertFalse(eos.feed(400, level = 60))
        assertFalse(eos.feed(100, level = 3_000))
        // Not speech yet, so no 1 s countdown: it waits for real speech (or the 5 s limit).
        assertFalse(eos.feed(2_000, level = 60))
        assertFalse(eos.feed(600, level = 3_000))
        assertFalse(eos.feed(980, level = 60))
        assertEquals(Endpoint.End, eos.onFrame(frame(60)))
    }
}
