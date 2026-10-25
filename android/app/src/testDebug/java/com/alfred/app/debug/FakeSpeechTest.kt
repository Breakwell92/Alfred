package com.alfred.app.debug

import kotlin.random.Random
import org.junit.Assert.assertTrue
import org.junit.Test

class FakeSpeechTest {
    @Test
    fun `levels look like speech - in range, with loud syllables and silent pauses`() {
        val speech = FakeSpeech(Random(42))
        val levels = List(60 * 20) { speech.next(1f / 60f) }

        assertTrue(levels.all { it in 0f..1f })
        assertTrue("never got loud", levels.max() > 0.7f)
        assertTrue("no pauses", levels.count { it == 0f } > 60)
        assertTrue("mostly silent", levels.count { it > 0.1f } > levels.size / 4)

        // Syllables: the level keeps rising and falling rather than holding.
        val turns = levels.zipWithNext().zipWithNext().count { (a, b) ->
            (b.first - a.first) > 0f && (b.second - b.first) < 0f
        }
        assertTrue("only $turns syllable peaks in 20 s", turns > 20)
    }
}
