package com.alfred.app.data.voice

/**
 * Decides when a hands-free turn has finished, from the mic frames being streamed. Push-to-talk
 * doesn't need one (the button ends the turn). One instance per turn.
 */
fun interface EndOfSpeechDetector {
    /** Feeds one PCM16 frame; true once the turn should end (stays true after that). */
    fun onFrame(pcm: ByteArray): Boolean
}

/**
 * A stopgap energy endpointer until Silero VAD (STU-69, ADR 0019). The noise floor is the
 * quietest 20 ms frame of the last 3 s, so a steady fan or TV sets its own level; a frame is
 * speech when it's well above that (and above an absolute minimum).
 *
 * The first [ignoreStartMillis] never count as speech: that's when the wake chime's echo and
 * the recorder's start-up land. Speech has "started" only after [minSpeechMillis] of it, so a
 * click or a cough doesn't start the silence countdown. The turn ends after [silenceMillis] of
 * quiet once speech has started, after [noSpeechMillis] if it never does, or at [maxMillis].
 */
class EnergyEndOfSpeech(
    private val silenceMillis: Int = 1_000,
    private val noSpeechMillis: Int = 5_000,
    private val maxMillis: Int = 15_000,
    private val minSpeechRms: Double = 300.0,
    private val speechOverNoise: Double = 3.0,
    private val ignoreStartMillis: Int = 300,
    private val minSpeechMillis: Int = 200,
) : EndOfSpeechDetector {
    private val recent = DoubleArray(FLOOR_WINDOW_FRAMES)
    private var frames = 0
    private var elapsedMillis = 0
    private var quietMillis = 0
    private var speechMillis = 0
    private var ended = false

    private val heardSpeech get() = speechMillis >= minSpeechMillis

    override fun onFrame(pcm: ByteArray): Boolean {
        if (ended) return true
        val samples = pcm.size / 2
        if (samples == 0) return false
        val millis = samples * 1000 / SAMPLE_RATE
        elapsedMillis += millis
        val level = pcm16Rms(pcm)
        // Digital silence (the recorder starting up) says nothing about the room's noise.
        if (level >= 1.0) recent[frames++ % FLOOR_WINDOW_FRAMES] = level
        var floor = Double.MAX_VALUE
        for (i in 0 until minOf(frames, FLOOR_WINDOW_FRAMES)) floor = minOf(floor, recent[i])
        val speech = elapsedMillis > ignoreStartMillis && level >= maxOf(minSpeechRms, floor * speechOverNoise)
        if (speech) {
            speechMillis += millis
            quietMillis = 0
        } else {
            quietMillis += millis
        }
        ended = elapsedMillis >= maxMillis ||
            (heardSpeech && quietMillis >= silenceMillis) ||
            (!heardSpeech && elapsedMillis >= noSpeechMillis)
        return ended
    }

    private companion object {
        const val SAMPLE_RATE = 16_000

        /** 3 s of 20 ms frames: longer than anyone talks without a breath. */
        const val FLOOR_WINDOW_FRAMES = 150
    }
}
