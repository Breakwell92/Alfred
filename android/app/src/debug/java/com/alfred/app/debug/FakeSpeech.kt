package com.alfred.app.debug

import kotlin.random.Random
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * A speech-like level envelope for exercising the orb without a mic or a reply (STU-113):
 * phrases of syllables (quick rise, slower fall, varied peaks) separated by pauses.
 */
class FakeSpeech(private val random: Random = Random.Default) {
    private var phraseLeft = 0f
    private var pauseLeft = 0f
    private var syllableLength = 0f
    private var syllableAt = 0f
    private var peak = 0f

    /** The level 0 to 1 after another [seconds] of speech. */
    fun next(seconds: Float): Float {
        if (pauseLeft > 0f) {
            pauseLeft -= seconds
            if (pauseLeft <= 0f) phraseLeft = random.nextFloat(1.2f, 3f)
            return 0f
        }
        phraseLeft -= seconds
        if (phraseLeft <= 0f) {
            pauseLeft = random.nextFloat(0.4f, 1.1f)
            syllableAt = syllableLength
            return 0f
        }
        syllableAt += seconds
        if (syllableAt >= syllableLength) {
            syllableAt = 0f
            syllableLength = random.nextFloat(0.12f, 0.28f)
            peak = random.nextFloat(0.35f, 1f)
        }
        val t = syllableAt / syllableLength
        val shape = if (t < 0.25f) t / 0.25f else 1f - (t - 0.25f) / 0.75f
        return (peak * shape).coerceIn(0f, 1f)
    }

    private fun Random.nextFloat(from: Float, until: Float) = from + nextFloat() * (until - from)
}

/** [FakeSpeech] levels about 60 times a second. */
fun fakeSpeechLevels(random: Random = Random.Default): Flow<Float> = flow {
    val speech = FakeSpeech(random)
    while (true) {
        emit(speech.next(FRAME_MS / 1000f))
        delay(FRAME_MS)
    }
}

private const val FRAME_MS = 16L
