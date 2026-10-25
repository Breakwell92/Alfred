package com.alfred.app.data.wakeword

import com.alfred.app.testing.FakeWakeWordModels
import com.alfred.app.testing.pcm
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class WakeWordDetectorTest {

    private val models = FakeWakeWordModels()
    private var now = 0L

    private fun detector(threshold: Float = 0.5f) = WakeWordDetector(models, threshold, clock = { now })

    /** Feeds [chunks] × 80 ms as 20 ms mic frames; returns any detections. */
    private fun WakeWordDetector.feed(chunks: Int, value: Short = 0): List<WakeDetection> =
        pcm(chunks, value).asList().chunked(640).mapNotNull { process(it.toByteArray()) }

    @Test
    fun `starts from noise embeddings`() {
        detector()
        // One mel pass over 4 s of noise, then the 16 embeddings the classifier starts with.
        assertEquals(listOf(64_000), models.melInputs.map { it.size })
        assertEquals(CLASSIFIER_WINDOW, models.embedInputs.size)
        assertEquals(List(CLASSIFIER_WINDOW) { EMBEDDING_WINDOW }, models.embedInputs.map { it.size })
    }

    @Test
    fun `scores once per 80 ms chunk with the right windows`() {
        val detector = detector()
        models.melInputs.clear()
        models.embedInputs.clear()

        detector.feed(chunks = 1)
        assertEquals(1, models.scoreInputs.size)
        // The first chunk has no context before it yet; later ones add 30 ms.
        assertEquals(listOf(1280), models.melInputs.map { it.size })
        detector.feed(chunks = 2)
        assertEquals(listOf(1280, 1760, 1760), models.melInputs.map { it.size })
        assertEquals(3, models.scoreInputs.size)
        assertEquals(List(3) { EMBEDDING_WINDOW }, models.embedInputs.map { it.size })
        assertEquals(List(3) { CLASSIFIER_WINDOW }, models.scoreInputs.map { it.size })
    }

    @Test
    fun `half a chunk waits for the rest`() {
        val detector = detector()
        detector.process(ByteArray(1280)) // 640 samples
        assertEquals(0, models.scoreInputs.size)
        detector.process(ByteArray(1280))
        assertEquals(1, models.scoreInputs.size)
    }

    @Test
    fun `audio reaches the mel model as PCM16 values`() {
        val detector = detector()
        detector.feed(chunks = 1, value = -1234)
        assertEquals(-1234f, models.melInputs.last()[0])
        assertEquals(-1234f, models.melInputs.last()[1279])
    }

    @Test
    fun `ignores the first five scores`() {
        models.defaultScore = 1f
        val detector = detector()
        assertEquals(emptyList<WakeDetection>(), detector.feed(chunks = WakeWordDetector.WARMUP_CHUNKS))
        assertEquals(listOf(WakeDetection(1f)), detector.feed(chunks = 1))
    }

    @Test
    fun `needs the threshold`() {
        val detector = detector(threshold = 0.6f)
        detector.feed(chunks = WakeWordDetector.WARMUP_CHUNKS)
        models.scores.addAll(listOf(0.59f, 0.6f))
        assertNull(detector.process(pcm(1)))
        assertEquals(WakeDetection(0.6f), detector.process(pcm(1)))
    }

    @Test
    fun `waits out the cooldown between wakes`() {
        models.defaultScore = 0.9f
        val detector = detector()
        detector.feed(chunks = WakeWordDetector.WARMUP_CHUNKS)
        assertNotNull(detector.process(pcm(1)))
        now += WakeWordDetector.DEFAULT_COOLDOWN_MILLIS - 1
        assertNull(detector.process(pcm(1)))
        now += 1
        assertNotNull(detector.process(pcm(1)))
    }

    @Test
    fun `reset forgets what was heard`() {
        models.defaultScore = 1f
        val detector = detector()
        detector.feed(chunks = 20) // the mel window is now all real (fake) frames
        assertEquals(models.melValue, models.embedInputs.last()[0][0])

        detector.reset()
        now += 10_000
        // Back to a mel window of ones, and warming up again.
        assertEquals(emptyList<WakeDetection>(), detector.feed(chunks = WakeWordDetector.WARMUP_CHUNKS))
        assertEquals(1f, models.embedInputs[models.embedInputs.size - WakeWordDetector.WARMUP_CHUNKS][0][0])
        assertEquals(listOf(WakeDetection(1f)), detector.feed(chunks = 1))
    }

    @Test
    fun `one long frame gives at most one wake`() {
        val detector = detector()
        detector.feed(chunks = WakeWordDetector.WARMUP_CHUNKS)
        models.scores.addAll(listOf(0.7f, 0.95f, 0.8f))
        assertEquals(WakeDetection(0.7f), detector.process(pcm(3)))
        // Every chunk was still scored, so the next frame carries on from the right place.
        assertEquals(WakeWordDetector.WARMUP_CHUNKS + 3, models.scoreInputs.size)
    }
}
