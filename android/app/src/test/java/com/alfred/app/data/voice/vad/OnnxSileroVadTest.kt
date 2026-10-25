package com.alfred.app.data.voice.vad

import com.alfred.app.data.voice.Endpoint
import com.alfred.app.data.wakeword.OnnxWakeWordModelsTest.Companion.silence
import com.alfred.app.data.wakeword.OnnxWakeWordModelsTest.Companion.wavPcm
import java.io.File
import kotlin.math.abs
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.BeforeClass
import org.junit.Test

/**
 * The real bundled Silero model on desktop ONNX Runtime, over the server's speech fixtures fed in
 * 20 ms frames like the microphone. Skipped on the JBR on Windows, like
 * [com.alfred.app.data.wakeword.OnnxWakeWordModelsTest].
 */
class OnnxSileroVadTest {

    private class Run(val endpoint: Endpoint, val endedAtMillis: Int, val report: EndpointReport?)

    private fun run(pcm: ByteArray, config: VadConfig = VadConfig()): Run {
        var report: EndpointReport? = null
        val eos = SileroEndOfSpeech(VadStream(model), config, onReport = { report = it })
        pcm.asList().chunked(FRAME).forEachIndexed { i, frame ->
            val endpoint = eos.onFrame(frame.toByteArray())
            if (endpoint != Endpoint.Continue) return Run(endpoint, (i + 1) * 20, report)
        }
        return Run(Endpoint.Continue, pcm.size / 32, report)
    }

    @Test
    fun `a long request with a pause at the comma is one turn`() {
        // "What is the weather going to be like in Manchester tomorrow morning, and will I need an umbrella?"
        val speech = trim(fixture("weather.wav"))
        val result = run(silence(1.0) + speech + silence(2.0))

        assertEquals(Endpoint.End, result.endpoint)
        val speechEnd = 1_000 + speech.size / 32
        assertTrue("ended at ${result.endedAtMillis}, speech ended at $speechEnd", result.endedAtMillis > speechEnd)
        // Within 0.8 s of finishing speaking.
        val after = result.endedAtMillis - speechEnd
        assertTrue("ended $after ms after the speech", after <= 800)
        assertTrue(checkNotNull(result.report?.latencyMillis) <= 800)
    }

    @Test
    fun `a hesitation between two phrases doesn't end the turn`() {
        val first = trim(fixture("lights.wav"))
        val second = trim(fixture("timer.wav"))
        val result = run(silence(1.0) + first + silence(0.45) + second + silence(2.0))

        assertEquals(Endpoint.End, result.endpoint)
        val secondEnd = 1_000 + (first.size + second.size) / 32 + 450
        assertTrue("ended at ${result.endedAtMillis}, speech ended at $secondEnd", result.endedAtMillis > secondEnd)
        assertTrue(result.endedAtMillis - secondEnd <= 800)
    }

    @Test
    fun `a quiet room is no speech`() {
        val result = run(silence(6.0))
        assertEquals(Endpoint.NoSpeech, result.endpoint)
        assertTrue("gave up at ${result.endedAtMillis}", result.endedAtMillis in 5_000..5_060)
    }

    private fun fixture(name: String) = wavPcm(File(SERVER_FIXTURES, name).readBytes())

    /** Cuts the clip's own leading and trailing silence, so the test sets the gaps. */
    private fun trim(pcm: ByteArray): ByteArray {
        fun sample(i: Int) = ((pcm[2 * i].toInt() and 0xFF) or (pcm[2 * i + 1].toInt() shl 8)).toShort().toInt()
        val samples = pcm.size / 2
        val first = (0 until samples).first { abs(sample(it)) > LOUD }
        val last = (samples - 1 downTo 0).first { abs(sample(it)) > LOUD }
        return pcm.copyOfRange(2 * first, 2 * (last + 1))
    }

    companion object {
        private const val FRAME = 640
        private const val LOUD = 500
        private val SERVER_FIXTURES = File("../../server/tests/fixtures/audio")
        private lateinit var model: OnnxSileroVad

        @BeforeClass
        @JvmStatic
        fun load() {
            // See OnnxWakeWordModelsTest: ONNX Runtime crashes the JBR on Windows; CI always runs this.
            val windows = System.getProperty("os.name").orEmpty().startsWith("Windows")
            val jbr = System.getProperty("java.vendor").orEmpty().contains("JetBrains")
            assumeFalse("ONNX Runtime can't load on the JBR on Windows", windows && jbr)
            model = OnnxSileroVad(File("src/main/assets/vad/$SILERO_MODEL_FILE").readBytes())
        }

        @AfterClass
        @JvmStatic
        fun close() {
            if (::model.isInitialized) model.close()
        }
    }
}
