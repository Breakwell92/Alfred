package com.alfred.app.data.wakeword

import kotlin.random.Random

/** A wake word heard: the classifier's score for the chunk that crossed the threshold. */
data class WakeDetection(val score: Float)

/**
 * openWakeWord's streaming pipeline (its `AudioFeatures` + `Model.predict`), fed 16 kHz PCM16
 * frames of any length. Audio is handled in 80 ms chunks (1280 samples). For each chunk:
 * mel spectrogram over the chunk plus 30 ms of context, one embedding over the last 76 mel
 * frames, and a classifier score over the last 16 embeddings. As in openWakeWord, the mel buffer
 * starts as ones, the embeddings start from those of random noise, and the first
 * [WARMUP_CHUNKS] scores after a start or [reset] are ignored.
 *
 * Not thread-safe: call it from one thread (the wake word loop).
 */
class WakeWordDetector(
    private val models: WakeWordModels,
    /** Score at or above which the wake word counts as heard. */
    @Volatile var threshold: Float = DEFAULT_THRESHOLD,
    private val cooldownMillis: Long = DEFAULT_COOLDOWN_MILLIS,
    private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
) {
    private val raw = FloatArray(CHUNK_SAMPLES + MEL_CONTEXT_SAMPLES)
    private var rawFilled = 0
    private val chunk = FloatArray(CHUNK_SAMPLES)
    private var chunkFilled = 0
    private val mel = ArrayDeque<FloatArray>(EMBEDDING_WINDOW + 16)
    private val features = ArrayDeque<FloatArray>(CLASSIFIER_WINDOW + 1)
    private val seedFeatures: List<FloatArray> = noiseEmbeddings()
    private var scored = 0
    private var lastDetectionAt: Long? = null

    /** The most recent score (for logging false accepts and tuning). */
    var lastScore: Float = 0f
        private set

    init {
        reset()
    }

    /** Forget the audio heard so far, e.g. before listening again after a turn. */
    fun reset() {
        rawFilled = 0
        chunkFilled = 0
        mel.clear()
        repeat(EMBEDDING_WINDOW) { mel.addLast(FloatArray(MEL_BINS) { 1f }) }
        features.clear()
        seedFeatures.forEach { features.addLast(it) }
        scored = 0
        lastScore = 0f
    }

    /** Feeds little-endian PCM16 [pcm]; returns a detection if one of its chunks heard the wake word. */
    fun process(pcm: ByteArray): WakeDetection? {
        var detection: WakeDetection? = null
        var i = 0
        while (i + 1 < pcm.size) {
            val sample = (pcm[i].toInt() and 0xFF) or (pcm[i + 1].toInt() shl 8)
            chunk[chunkFilled++] = sample.toShort().toFloat()
            i += 2
            if (chunkFilled == CHUNK_SAMPLES) {
                chunkFilled = 0
                // Keep going after a wake so the buffers stay in step (the cooldown stops a second one).
                processChunk()?.let { if (detection == null) detection = it }
            }
        }
        return detection
    }

    private fun processChunk(): WakeDetection? {
        // Keep the last chunk plus context, newest at the end.
        val keep = minOf(rawFilled, MEL_CONTEXT_SAMPLES)
        System.arraycopy(raw, rawFilled - keep, raw, 0, keep)
        System.arraycopy(chunk, 0, raw, keep, CHUNK_SAMPLES)
        rawFilled = keep + CHUNK_SAMPLES

        models.melSpectrogram(raw.copyOf(rawFilled)).forEach { mel.addLast(it) }
        while (mel.size > EMBEDDING_WINDOW) mel.removeFirst()

        features.addLast(models.embed(mel.toTypedArray()))
        while (features.size > CLASSIFIER_WINDOW) features.removeFirst()

        val score = models.score(features.toTypedArray())
        lastScore = score
        scored++
        if (scored <= WARMUP_CHUNKS || score < threshold) return null
        val now = clock()
        val last = lastDetectionAt
        if (last != null && now - last < cooldownMillis) return null
        lastDetectionAt = now
        return WakeDetection(score)
    }

    /** Embeddings of 4 s of random noise, the classifier's starting context (as in openWakeWord). */
    private fun noiseEmbeddings(): List<FloatArray> {
        val random = Random(SEED)
        val noise = FloatArray(SAMPLE_RATE * 4) { random.nextInt(-1000, 1000).toFloat() }
        val spec = models.melSpectrogram(noise)
        return (CLASSIFIER_WINDOW - 1 downTo 0).map { back ->
            val end = spec.size - EMBEDDING_STEP * back
            models.embed(Array(EMBEDDING_WINDOW) { spec[end - EMBEDDING_WINDOW + it] })
        }
    }

    companion object {
        const val SAMPLE_RATE = 16_000

        /** 80 ms: openWakeWord's step. */
        const val CHUNK_SAMPLES = 1280

        /** Extra samples before each chunk so the mel frames line up across chunks. */
        const val MEL_CONTEXT_SAMPLES = 480

        /** Mel frames between consecutive embeddings (one chunk). */
        const val EMBEDDING_STEP = 8

        /** Scores ignored after a start or reset, while the buffers fill with real audio. */
        const val WARMUP_CHUNKS = 5

        const val DEFAULT_THRESHOLD = 0.5f
        const val DEFAULT_COOLDOWN_MILLIS = 2_000L
        private const val SEED = 68
    }
}
