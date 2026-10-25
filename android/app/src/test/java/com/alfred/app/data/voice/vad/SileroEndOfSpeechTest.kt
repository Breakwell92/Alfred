package com.alfred.app.data.voice.vad

import com.alfred.app.data.voice.Endpoint
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The streaming around the model, with a fake [VadModel] that calls loud chunks speech. */
class SileroEndOfSpeechTest {

    private class FakeVadModel : VadModel {
        val inputs = mutableListOf<FloatArray>()
        val states = mutableListOf<FloatArray>()

        override fun probability(input: FloatArray, state: FloatArray): Float {
            inputs += input.copyOf()
            states += state.copyOf()
            state.fill(inputs.size.toFloat())
            val loud = input.drop(VAD_CONTEXT_SAMPLES).map { kotlin.math.abs(it) }.average() > 0.1
            return if (loud) 0.95f else 0.02f
        }

        override fun close() = Unit
    }

    /** One 20 ms frame where sample i is [value] (PCM16). */
    private fun frame(value: (Int) -> Int): ByteArray = ByteArray(640).also { out ->
        for (i in 0 until 320) {
            val v = value(i)
            out[2 * i] = v.toByte()
            out[2 * i + 1] = (v shr 8).toByte()
        }
    }

    private fun frames(millis: Int, level: Int) = List(millis / 20) { frame { level } }

    @Test
    fun `20 ms frames become 512-sample chunks with the previous 64 samples in front`() {
        val model = FakeVadModel()
        val stream = VadStream(model)
        var counter = 0
        val chunks = mutableListOf<Float>()
        // 8 frames = 2560 samples = 5 chunks; samples count up so their positions can be checked.
        repeat(8) { stream.feed(frame { counter++ }) { chunks += it } }

        assertEquals(5, chunks.size)
        val first = model.inputs[0]
        assertEquals(VAD_CONTEXT_SAMPLES + VAD_CHUNK_SAMPLES, first.size)
        assertTrue(first.take(VAD_CONTEXT_SAMPLES).all { it == 0f })
        assertEquals(0f, first[VAD_CONTEXT_SAMPLES])
        assertEquals(511 / 32768f, first.last())
        // The second chunk starts with the first one's last 64 samples.
        val second = model.inputs[1]
        assertEquals(448 / 32768f, second[0])
        assertEquals(512 / 32768f, second[VAD_CONTEXT_SAMPLES])
        // The recurrent state is carried from chunk to chunk.
        assertArrayEquals(FloatArray(VAD_STATE_SIZE), model.states[0], 0f)
        assertArrayEquals(FloatArray(VAD_STATE_SIZE) { 1f }, model.states[1], 0f)
    }

    @Test
    fun `ends after speech and silence, and reports once`() {
        val reports = mutableListOf<EndpointReport>()
        var now = 0L
        val eos = SileroEndOfSpeech(VadStream(FakeVadModel()), VadConfig(), reports::add) { now.also { now += 1_000 } }

        val results = (frames(400, 20) + frames(1_200, 8_000) + frames(1_000, 20)).map(eos::onFrame)

        assertEquals(Endpoint.End, results.last())
        val firstEnd = results.indexOfFirst { it != Endpoint.Continue }
        // 400 + 1 200 ms in, plus 700 ms of quiet (rounded up to whole chunks).
        assertTrue("ended at frame $firstEnd", firstEnd * 20 in 2_300..2_400)
        assertEquals(Endpoint.End, eos.onFrame(frame { 8_000 }))
        assertEquals(1, reports.size)
        assertEquals("silence", reports[0].reason)
        assertTrue(reports[0].meanInferenceMicros > 0)
    }

    @Test
    fun `a pause while held reports nothing, going quiet after the release reports once`() {
        val reports = mutableListOf<EndpointReport>()
        var held = true
        val eos = SileroEndOfSpeech(VadStream(FakeVadModel()), VadConfig(), reports::add, held = { held })

        val whileHeld = (frames(1_000, 8_000) + frames(1_500, 20) + frames(500, 8_000)).map(eos::onFrame)
        assertTrue(whileHeld.all { it == Endpoint.Continue })
        assertTrue(reports.isEmpty())

        held = false
        val afterRelease = frames(1_000, 20).map(eos::onFrame)
        assertEquals(Endpoint.End, afterRelease.last())
        assertEquals(1, reports.size)
        assertEquals("silence", reports[0].reason)
    }

    @Test
    fun `gives up when nobody speaks`() {
        val eos = SileroEndOfSpeech(VadStream(FakeVadModel()), VadConfig(noSpeechMillis = 2_000))
        val results = frames(2_100, 20).map(eos::onFrame)
        assertEquals(Endpoint.NoSpeech, results.last())
    }
}
