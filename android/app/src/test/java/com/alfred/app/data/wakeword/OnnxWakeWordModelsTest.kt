package com.alfred.app.data.wakeword

import java.io.File
import org.junit.AfterClass
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.BeforeClass
import org.junit.Test

/**
 * The real bundled models on desktop ONNX Runtime: the stock "hey jarvis" model hears a
 * synthetic "Hey Jarvis" and stays quiet for an ordinary request. Unit tests run from the
 * module directory (`android/app`).
 */
class OnnxWakeWordModelsTest {

    @Test
    fun heyJarvisIsHeard() {
        val score = maxScore(silence(1.0) + wavPcm(resource("wakeword/hey_jarvis.wav")) + silence(1.0))
        assertTrue("hey jarvis scored $score", score >= WakeWordDetector.DEFAULT_THRESHOLD)
    }

    @Test
    fun otherSpeechIsNotHeard() {
        val score = maxScore(silence(1.0) + wavPcm(File(SERVER_FIXTURES, "timer.wav").readBytes()) + silence(1.0))
        assertTrue("timer request scored $score", score < 0.2f)
    }

    @Test
    fun quietRoomIsNotHeard() {
        val score = maxScore(silence(5.0))
        assertTrue("silence scored $score", score < 0.2f)
    }

    /** Highest score over the clip, fed in 20 ms frames like the microphone. */
    private fun maxScore(pcm: ByteArray): Float {
        val detector = WakeWordDetector(models, threshold = 2f) // never "detects": we want every score
        var max = 0f
        pcm.asList().chunked(FRAME).forEach { frame ->
            detector.process(frame.toByteArray())
            max = maxOf(max, detector.lastScore)
        }
        return max
    }

    private fun resource(path: String): ByteArray =
        checkNotNull(javaClass.classLoader?.getResourceAsStream(path)) { "missing $path" }.use { it.readBytes() }

    companion object {
        private const val FRAME = 640
        private val ASSETS = File("src/main/assets/wakeword")
        private val SERVER_FIXTURES = File("../../server/tests/fixtures/audio")
        private lateinit var models: OnnxWakeWordModels

        @BeforeClass
        @JvmStatic
        fun load() {
            // The JBR on Windows ships an old msvcp140.dll that ONNX Runtime crashes on (it takes
            // the whole test JVM down). CI (Linux) always runs these; see android/README.md.
            val windows = System.getProperty("os.name").orEmpty().startsWith("Windows")
            val jbr = System.getProperty("java.vendor").orEmpty().contains("JetBrains")
            assumeFalse("ONNX Runtime can't load on the JBR on Windows", windows && jbr)
            models = OnnxWakeWordModels({ File(ASSETS, it).readBytes() }, StockWakeWord.HeyJarvis.modelFile)
        }

        @AfterClass
        @JvmStatic
        fun close() {
            if (::models.isInitialized) models.close()
        }

        /** Low-level noise (a quiet room), PCM16 LE. */
        fun silence(seconds: Double): ByteArray {
            val random = java.util.Random(1)
            val samples = (16_000 * seconds).toInt()
            return ByteArray(samples * 2).also { out ->
                for (i in 0 until samples) {
                    val v = (random.nextGaussian() * 30).toInt()
                    out[2 * i] = v.toByte()
                    out[2 * i + 1] = (v shr 8).toByte()
                }
            }
        }

        /** The `data` chunk of a 16 kHz mono PCM16 WAV. */
        fun wavPcm(wav: ByteArray): ByteArray {
            var offset = 12
            while (offset + 8 <= wav.size) {
                val id = String(wav, offset, 4, Charsets.US_ASCII)
                val size = (wav[offset + 4].toInt() and 0xFF) or
                    ((wav[offset + 5].toInt() and 0xFF) shl 8) or
                    ((wav[offset + 6].toInt() and 0xFF) shl 16) or
                    ((wav[offset + 7].toInt() and 0xFF) shl 24)
                if (id == "data") return wav.copyOfRange(offset + 8, minOf(wav.size, offset + 8 + size))
                offset += 8 + size + (size and 1)
            }
            error("no data chunk")
        }
    }
}
