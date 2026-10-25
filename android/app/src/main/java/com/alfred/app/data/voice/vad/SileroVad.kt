package com.alfred.app.data.voice.vad

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import com.alfred.app.data.voice.EndOfSpeechDetector
import com.alfred.app.data.voice.Endpoint
import java.nio.FloatBuffer
import java.nio.LongBuffer

/** Samples Silero VAD scores at a time at 16 kHz: 32 ms. */
const val VAD_CHUNK_SAMPLES = 512

/** Samples of the previous chunk prepended to each one, as Silero's own wrapper does. */
const val VAD_CONTEXT_SAMPLES = 64

/** Floats in Silero v5's recurrent state, `[2, 1, 128]`. */
const val VAD_STATE_SIZE = 2 * 128

const val VAD_CHUNK_MILLIS = VAD_CHUNK_SAMPLES * 1000 / 16_000

const val SILERO_MODEL_FILE = "silero_vad.onnx"

/** Silero VAD (ADR 0024), behind an interface so the streaming can be tested without ONNX. */
interface VadModel : AutoCloseable {
    /**
     * Speech probability, 0 to 1, of one chunk: [input] is [VAD_CONTEXT_SAMPLES] +
     * [VAD_CHUNK_SAMPLES] samples scaled to ±1. [state] ([VAD_STATE_SIZE]) is read and replaced
     * with the next state.
     */
    fun probability(input: FloatArray, state: FloatArray): Float
}

/**
 * Silero VAD v5 on ONNX Runtime. The session is created once and shared by every turn (one turn
 * at a time); each turn's state lives in its own [VadStream]. Single-threaded like the wake word
 * models: a chunk takes a fraction of a millisecond.
 */
class OnnxSileroVad(model: ByteArray) : VadModel {
    private val env = OrtEnvironment.getEnvironment()
    private val options = OrtSession.SessionOptions().apply {
        setIntraOpNumThreads(1)
        setInterOpNumThreads(1)
    }
    private val session = env.createSession(model, options)
    private val sampleRate = OnnxTensor.createTensor(env, LongBuffer.wrap(longArrayOf(16_000)), longArrayOf())

    override fun probability(input: FloatArray, state: FloatArray): Float {
        val audio = OnnxTensor.createTensor(env, FloatBuffer.wrap(input), longArrayOf(1, input.size.toLong()))
        val stateIn = OnnxTensor.createTensor(env, FloatBuffer.wrap(state), longArrayOf(2, 1, 128))
        try {
            session.run(mapOf("input" to audio, "state" to stateIn, "sr" to sampleRate)).use { result ->
                val probability = (result[0] as OnnxTensor).floatBuffer.get(0)
                (result[1] as OnnxTensor).floatBuffer.get(state)
                return probability
            }
        } finally {
            audio.close()
            stateIn.close()
        }
    }

    override fun close() {
        sampleRate.close()
        session.close()
        options.close()
    }
}

/**
 * One turn's audio through a [VadModel]: 20 ms PCM16 frames in, a probability per 32 ms chunk
 * out. Holds the turn's recurrent state, the context carried between chunks and the samples
 * still waiting for a full chunk.
 */
class VadStream(private val model: VadModel) {
    private val state = FloatArray(VAD_STATE_SIZE)
    private val input = FloatArray(VAD_CONTEXT_SAMPLES + VAD_CHUNK_SAMPLES)
    private var filled = 0

    /** Calls [onChunk] with each chunk's probability that [pcm] completes (none, one or more). */
    inline fun feed(pcm: ByteArray, onChunk: (Float) -> Unit) {
        var i = 0
        while (i + 1 < pcm.size) {
            val sample = (pcm[i].toInt() and 0xFF) or (pcm[i + 1].toInt() shl 8)
            i += 2
            if (add(sample.toShort() / 32768f)) onChunk(score())
        }
    }

    /** Adds one sample; true when a chunk is full. */
    @PublishedApi
    internal fun add(sample: Float): Boolean {
        input[VAD_CONTEXT_SAMPLES + filled++] = sample
        return filled == VAD_CHUNK_SAMPLES
    }

    /** Scores the full chunk and keeps its last samples as the next one's context. */
    @PublishedApi
    internal fun score(): Float {
        val probability = model.probability(input, state)
        input.copyInto(input, 0, VAD_CHUNK_SAMPLES, VAD_CHUNK_SAMPLES + VAD_CONTEXT_SAMPLES)
        filled = 0
        return probability
    }
}

/**
 * Ends a turn from Silero VAD's scores ([EndpointStateMachine]). Calls [onReport] once, when the
 * turn ends, with how it was decided.
 */
class SileroEndOfSpeech(
    private val stream: VadStream,
    config: VadConfig,
    private val onReport: (EndpointReport) -> Unit = {},
    private val nanoClock: () -> Long = System::nanoTime,
) : EndOfSpeechDetector {
    private val machine = EndpointStateMachine(config)
    private var result = Endpoint.Continue
    private var chunks = 0
    private var inferenceNanos = 0L

    override fun onFrame(pcm: ByteArray): Endpoint {
        if (result != Endpoint.Continue) return result
        var started = nanoClock()
        stream.feed(pcm) { probability ->
            val now = nanoClock()
            inferenceNanos += now - started
            chunks++
            if (result == Endpoint.Continue) result = machine.onChunk(probability, VAD_CHUNK_MILLIS)
            started = now
        }
        if (result != Endpoint.Continue) {
            machine.report?.let { onReport(it.copy(meanInferenceMicros = inferenceNanos / 1_000 / maxOf(chunks, 1))) }
        }
        return result
    }
}
