package com.alfred.app.data.voice.vad

import com.alfred.app.data.voice.Endpoint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The turn state machine with synthetic VAD scores, one per 32 ms chunk. */
class EndpointStateMachineTest {

    private val machine = EndpointStateMachine(VadConfig())

    /** Feeds [millis] of chunks scoring [probability]; the first non-Continue result, if any. */
    private fun EndpointStateMachine.feed(millis: Int, probability: Float): Endpoint? {
        var result: Endpoint? = null
        repeat(millis / VAD_CHUNK_MILLIS) {
            val endpoint = onChunk(probability, VAD_CHUNK_MILLIS)
            if (result == null && endpoint != Endpoint.Continue) result = endpoint
        }
        return result
    }

    @Test
    fun `listening until speech, then speaking`() {
        assertNull(machine.feed(1_000, 0.02f))
        assertEquals(EndpointStateMachine.State.Listening, machine.state)
        assertNull(machine.feed(320, 0.95f))
        assertEquals(EndpointStateMachine.State.Speaking, machine.state)
    }

    @Test
    fun `ends after the silence, within 0_8 s of the last speech`() {
        machine.feed(500, 0.02f)
        machine.feed(1_500, 0.95f)
        assertNull(machine.feed(640, 0.02f))
        assertEquals(Endpoint.End, machine.feed(96, 0.02f))

        val report = checkNotNull(machine.report)
        assertEquals("silence", report.reason)
        val latency = checkNotNull(report.latencyMillis)
        assertTrue("latency $latency", latency in 700..800)
        assertEquals(report.totalMillis - latency, report.lastSpeechMillis)
        // 15 quiet chunks, then the speech run that started it.
        assertEquals(480, report.speechStartMillis)
    }

    @Test
    fun `a natural pause mid-sentence doesn't end the turn`() {
        // "Turn on the…" (a 500 ms hesitation) "…lounge lights"
        machine.feed(300, 0.02f)
        machine.feed(900, 0.9f)
        assertNull(machine.feed(500, 0.05f))
        assertNull(machine.feed(800, 0.9f))
        assertEquals(EndpointStateMachine.State.Speaking, machine.state)
        assertEquals(Endpoint.End, machine.feed(800, 0.05f))
    }

    @Test
    fun `scores between the two thresholds keep speech going`() {
        machine.feed(640, 0.9f)
        // A trailing vowel: below the speech threshold, above the quiet one.
        assertNull(machine.feed(1_500, 0.4f))
        assertEquals(Endpoint.End, machine.feed(800, 0.3f))
    }

    @Test
    fun `a click or a cough doesn't start speech`() {
        machine.feed(500, 0.02f)
        machine.feed(96, 0.99f)
        machine.feed(500, 0.02f)
        machine.feed(160, 0.99f)
        assertEquals(EndpointStateMachine.State.Listening, machine.state)
        // So no silence countdown: it waits for real speech.
        assertNull(machine.feed(1_500, 0.02f))
    }

    @Test
    fun `gives up quietly when nobody speaks`() {
        assertNull(machine.feed(4_960, 0.1f))
        assertEquals(Endpoint.NoSpeech, machine.feed(64, 0.1f))
        val report = checkNotNull(machine.report)
        assertEquals("no_speech", report.reason)
        assertNull(report.speechStartMillis)
        assertNull(report.latencyMillis)
    }

    @Test
    fun `stops at the longest utterance`() {
        val short = EndpointStateMachine(VadConfig(maxUtteranceMillis = 10_000))
        short.feed(320, 0.9f)
        assertEquals(Endpoint.End, short.feed(10_000, 0.9f))
        assertEquals("max_length", short.report?.reason)
    }

    @Test
    fun `the result sticks`() {
        machine.feed(5_024, 0.0f)
        assertEquals(Endpoint.NoSpeech, machine.onChunk(0.99f, VAD_CHUNK_MILLIS))
        assertEquals(EndpointStateMachine.State.Ended, machine.state)
    }

    @Test
    fun `push-to-talk limits off never give up`() {
        val ptt = EndpointStateMachine(VadConfig(noSpeechMillis = Int.MAX_VALUE, maxUtteranceMillis = Int.MAX_VALUE))
        assertNull(ptt.feed(60_000, 0.01f))
        ptt.feed(500, 0.9f)
        assertEquals(Endpoint.End, ptt.feed(800, 0.01f))
    }
}
