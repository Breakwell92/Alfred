package com.alfred.app.data.voice

/** What an [EndOfSpeechDetector] makes of the turn so far. */
enum class Endpoint {
    /** Keep listening. */
    Continue,

    /** The speaker has finished (or the turn hit its length limit): send it. */
    End,

    /** Nobody spoke: cancel the turn quietly instead of sending silence. */
    NoSpeech,
}

/**
 * Decides when a turn being listened to has finished, from the mic frames being streamed. One
 * instance per turn. Silero VAD ([com.alfred.app.data.voice.vad.SileroEndOfSpeech], ADR 0023)
 * in the app; [EnergyEndOfSpeech] when the model can't run.
 */
fun interface EndOfSpeechDetector {
    /** Feeds one PCM16 frame. Once it returns something other than [Endpoint.Continue], it keeps returning that. */
    fun onFrame(pcm: ByteArray): Endpoint
}

/**
 * For turns the user starts on screen (push-to-talk, CUT IN), when Silero isn't available or in
 * tests: silence after speech ends the turn (the Recording screen's "or go quiet"); nothing does
 * before anyone speaks, and [VoiceController] caps the length. The app uses
 * [com.alfred.app.data.voice.vad.EndOfSpeechFactory.pushToTalk].
 */
fun goQuiet(): EndOfSpeechDetector = EnergyEndOfSpeech(noSpeechMillis = Int.MAX_VALUE, maxMillis = Int.MAX_VALUE)

/**
 * An energy endpointer, the fallback when Silero VAD can't load (ADR 0023). The noise floor is the
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
    private var result = Endpoint.Continue

    private val heardSpeech get() = speechMillis >= minSpeechMillis

    override fun onFrame(pcm: ByteArray): Endpoint {
        if (result != Endpoint.Continue) return result
        val samples = pcm.size / 2
        if (samples == 0) return result
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
        result = when {
            heardSpeech && (quietMillis >= silenceMillis || elapsedMillis >= maxMillis) -> Endpoint.End
            !heardSpeech && elapsedMillis >= minOf(noSpeechMillis, maxMillis) -> Endpoint.NoSpeech
            else -> Endpoint.Continue
        }
        return result
    }

    private companion object {
        const val SAMPLE_RATE = 16_000

        /** 3 s of 20 ms frames: longer than anyone talks without a breath. */
        const val FLOOR_WINDOW_FRAMES = 150
    }
}
