package com.alfred.app.data.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackLevelsTest {

    private val loud = pcm(*IntArray(WINDOW) { if (it % 2 == 0) 20_000 else -20_000 })
    private val quiet = pcm(*IntArray(WINDOW) { if (it % 2 == 0) 400 else -400 })
    private val silent = ByteArray(WINDOW * 2)

    @Test
    fun `the level follows the playback position, not what was written last`() {
        val levels = PlaybackLevels(WINDOW)
        levels.add(loud)
        levels.add(silent)
        levels.add(quiet)

        assertEquals(pcm16Level(loud), levels.levelAt(0), 0f)
        assertEquals(pcm16Level(loud), levels.levelAt(WINDOW - 1L), 0f)
        assertEquals(0f, levels.levelAt(WINDOW.toLong()), 0f)
        assertEquals(pcm16Level(quiet), levels.levelAt(2L * WINDOW + 5), 0f)
    }

    @Test
    fun `past the end of what was written is silence`() {
        val levels = PlaybackLevels(WINDOW)
        levels.add(loud)

        assertEquals(0f, levels.levelAt(3L * WINDOW), 0f)
        // Windows written later still line up with their own position.
        levels.add(loud)
        levels.add(loud)
        levels.add(quiet)
        assertEquals(pcm16Level(quiet), levels.levelAt(3L * WINDOW), 0f)
    }

    @Test
    fun `writes that split a window are joined`() {
        val levels = PlaybackLevels(WINDOW)
        levels.add(loud.copyOfRange(0, 6))
        assertEquals("a part-window isn't scored yet", 0f, levels.levelAt(0), 0f)
        levels.add(loud.copyOfRange(6, loud.size) + quiet.copyOfRange(0, 10))
        levels.add(quiet.copyOfRange(10, quiet.size))

        assertEquals(pcm16Level(loud), levels.levelAt(0), 0f)
        assertEquals(pcm16Level(quiet), levels.levelAt(WINDOW.toLong()), 0f)
    }

    @Test
    fun `reset starts a new reply at position 0`() {
        val levels = PlaybackLevels(WINDOW)
        levels.add(loud)
        levels.add(loud)
        levels.levelAt(WINDOW.toLong())
        levels.add(loud.copyOfRange(0, 4))

        levels.reset()
        levels.add(quiet)

        assertEquals(pcm16Level(quiet), levels.levelAt(0), 0f)
    }

    private companion object {
        const val WINDOW = 8
    }
}
