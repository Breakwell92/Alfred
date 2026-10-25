package com.alfred.app.data.voice.vad

import com.alfred.app.data.voice.Endpoint

/** How the VAD endpointer decides (ADR 0024). Tunable on the developer screen. */
data class VadConfig(
    /** A chunk is speech at this probability or above. */
    val threshold: Float = DEFAULT_THRESHOLD,
    /** Quiet after speech that ends the turn. */
    val endSilenceMillis: Int = DEFAULT_END_SILENCE_MILLIS,
    /** Give up when speech hasn't started by then. */
    val noSpeechMillis: Int = DEFAULT_NO_SPEECH_MILLIS,
    /** The longest a turn may run, speech or not. */
    val maxUtteranceMillis: Int = DEFAULT_MAX_UTTERANCE_MILLIS,
    /** Speech has started only after this much of it in a row, so a click or a cough doesn't count. */
    val minSpeechMillis: Int = DEFAULT_MIN_SPEECH_MILLIS,
) {
    /**
     * Once speaking, a chunk is quiet only below this, Silero's own hysteresis: probabilities
     * between the two (a soft consonant, a trailing vowel) keep the speech going.
     */
    val quietThreshold: Float get() = (threshold - HYSTERESIS).coerceAtLeast(0.01f)

    companion object {
        const val DEFAULT_THRESHOLD = 0.5f
        const val DEFAULT_END_SILENCE_MILLIS = 700
        const val DEFAULT_NO_SPEECH_MILLIS = 5_000
        const val DEFAULT_MAX_UTTERANCE_MILLIS = 30_000
        const val DEFAULT_MIN_SPEECH_MILLIS = 250
        private const val HYSTERESIS = 0.15f
    }
}

/** How a turn's end was decided, logged per turn (ADR 0024). Times are from the mic opening. */
data class EndpointReport(
    val endpoint: Endpoint,
    /** Why: "silence", "max_length" or "no_speech". */
    val reason: String,
    val totalMillis: Int,
    /** When speech started (counted from the start of its run), or null if it never did. */
    val speechStartMillis: Int?,
    /** The end of the last speech chunk, or null if speech never started. */
    val lastSpeechMillis: Int?,
    /** From the last speech to the decision: how long after the speaker stopped the turn ended. */
    val latencyMillis: Int?,
    /** Mean time per model call, microseconds (0 for synthetic scores). */
    val meanInferenceMicros: Long = 0,
)

/**
 * The turn as a state machine over VAD probabilities: Listening → (speech for
 * [VadConfig.minSpeechMillis]) Speaking → (quiet for [VadConfig.endSilenceMillis]) end. No
 * speech by [VadConfig.noSpeechMillis] gives [Endpoint.NoSpeech]; [VadConfig.maxUtteranceMillis]
 * ends it either way. Pure, so it's tested with synthetic scores.
 */
class EndpointStateMachine(private val config: VadConfig) {

    enum class State { Listening, Speaking, Ended }

    var state: State = State.Listening
        private set

    /** Set once the turn has ended. */
    var report: EndpointReport? = null
        private set

    private var elapsed = 0
    private var run = 0
    private var speechStart: Int? = null
    private var lastSpeech: Int? = null

    /** One chunk of [millis] with speech [probability]; the result sticks once it isn't Continue. */
    fun onChunk(probability: Float, millis: Int): Endpoint {
        report?.let { return it.endpoint }
        elapsed += millis
        when (state) {
            State.Listening -> {
                run = if (probability >= config.threshold) run + millis else 0
                if (run >= config.minSpeechMillis) {
                    state = State.Speaking
                    speechStart = elapsed - run
                    lastSpeech = elapsed
                }
            }
            State.Speaking -> if (probability >= config.quietThreshold) lastSpeech = elapsed
            State.Ended -> Unit
        }
        val last = lastSpeech
        return when {
            state == State.Speaking && last != null && elapsed - last >= config.endSilenceMillis ->
                end(Endpoint.End, "silence")
            elapsed >= config.maxUtteranceMillis && state == State.Speaking -> end(Endpoint.End, "max_length")
            state == State.Listening && elapsed >= minOf(config.noSpeechMillis, config.maxUtteranceMillis) ->
                end(Endpoint.NoSpeech, "no_speech")
            else -> Endpoint.Continue
        }
    }

    private fun end(endpoint: Endpoint, reason: String): Endpoint {
        state = State.Ended
        report = EndpointReport(
            endpoint = endpoint,
            reason = reason,
            totalMillis = elapsed,
            speechStartMillis = speechStart,
            lastSpeechMillis = lastSpeech,
            latencyMillis = lastSpeech?.let { elapsed - it },
        )
        return endpoint
    }
}
