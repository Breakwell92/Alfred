package com.alfred.app.data.wakeword

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.nio.FloatBuffer

/** Mel bins per spectrogram frame (openWakeWord's melspectrogram model). */
const val MEL_BINS = 32

/** Values in one speech embedding (openWakeWord's embedding model). */
const val EMBEDDING_SIZE = 96

/** Mel frames the embedding model looks at: 76 frames of 10 ms, about 0.775 s. */
const val EMBEDDING_WINDOW = 76

/** Embeddings the wake word classifiers look at: 16 × 80 ms, about 1.3 s. */
const val CLASSIFIER_WINDOW = 16

/**
 * The three models in openWakeWord's pipeline (docs/decisions/0019-wake-word.md): audio →
 * mel spectrogram → speech embedding → wake word score. Behind an interface so the streaming
 * logic in [WakeWordDetector] can be tested without ONNX.
 */
interface WakeWordModels : AutoCloseable {
    /** [samples] are PCM16 values as floats (not scaled to ±1). Returns frames × [MEL_BINS]. */
    fun melSpectrogram(samples: FloatArray): Array<FloatArray>

    /** One [EMBEDDING_WINDOW] × [MEL_BINS] window → an [EMBEDDING_SIZE] embedding. */
    fun embed(window: Array<FloatArray>): FloatArray

    /** The last [CLASSIFIER_WINDOW] embeddings → wake word score, 0 to 1. */
    fun score(features: Array<FloatArray>): Float
}

/** The stock openWakeWord models bundled in `assets/wakeword/` (ADR 0019). */
enum class StockWakeWord(val label: String, val modelFile: String) {
    HeyJarvis("Hey Jarvis", "hey_jarvis_v0.1.onnx"),
    Alexa("Alexa", "alexa_v0.1.onnx"),
    HeyMycroft("Hey Mycroft", "hey_mycroft_v0.1.onnx"),
}

const val MEL_MODEL_FILE = "melspectrogram.onnx"
const val EMBEDDING_MODEL_FILE = "embedding_model.onnx"

/**
 * [WakeWordModels] on ONNX Runtime. The sessions are created once and reused for every frame;
 * each runs on a single thread, which is plenty for one 80 ms chunk and keeps the CPU cost low.
 * [load] reads a model file by name (from the APK's assets in the app, from disk in tests).
 */
class OnnxWakeWordModels(load: (String) -> ByteArray, wakeWordFile: String) : WakeWordModels {

    private val env = OrtEnvironment.getEnvironment()
    private val options = OrtSession.SessionOptions().apply {
        setIntraOpNumThreads(1)
        setInterOpNumThreads(1)
    }
    private val mel = env.createSession(load(MEL_MODEL_FILE), options)
    private val embedding = env.createSession(load(EMBEDDING_MODEL_FILE), options)
    private val classifier = env.createSession(load(wakeWordFile), options)

    override fun melSpectrogram(samples: FloatArray): Array<FloatArray> {
        val shape = longArrayOf(1, samples.size.toLong())
        val out = run(mel, FloatBuffer.wrap(samples), shape)
        // Output is [1, 1, frames, 32]; openWakeWord rescales it before the embedding model.
        val frames = out.size / MEL_BINS
        return Array(frames) { f -> FloatArray(MEL_BINS) { b -> out[f * MEL_BINS + b] / 10f + 2f } }
    }

    override fun embed(window: Array<FloatArray>): FloatArray {
        require(window.size == EMBEDDING_WINDOW) { "Embedding needs $EMBEDDING_WINDOW mel frames" }
        val input = FloatBuffer.allocate(EMBEDDING_WINDOW * MEL_BINS)
        window.forEach(input::put)
        input.rewind()
        // Input [1, 76, 32, 1]; output [1, 1, 1, 96].
        return run(embedding, input, longArrayOf(1, EMBEDDING_WINDOW.toLong(), MEL_BINS.toLong(), 1))
    }

    override fun score(features: Array<FloatArray>): Float {
        require(features.size == CLASSIFIER_WINDOW) { "Classifier needs $CLASSIFIER_WINDOW embeddings" }
        val input = FloatBuffer.allocate(CLASSIFIER_WINDOW * EMBEDDING_SIZE)
        features.forEach(input::put)
        input.rewind()
        return run(classifier, input, longArrayOf(1, CLASSIFIER_WINDOW.toLong(), EMBEDDING_SIZE.toLong()))[0]
    }

    private fun run(session: OrtSession, input: FloatBuffer, shape: LongArray): FloatArray =
        OnnxTensor.createTensor(env, input, shape).use { tensor ->
            session.run(mapOf(session.inputNames.first() to tensor)).use { result ->
                val buffer = (result[0] as OnnxTensor).floatBuffer
                FloatArray(buffer.remaining()).also(buffer::get)
            }
        }

    override fun close() {
        classifier.close()
        embedding.close()
        mel.close()
        options.close()
    }
}
