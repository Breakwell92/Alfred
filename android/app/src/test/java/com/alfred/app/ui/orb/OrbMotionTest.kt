package com.alfred.app.ui.orb

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OrbMotionTest {
    private val frame = 1f / 60f

    private fun OrbMotion.run(seconds: Float, state: OrbState, level: Float = 0f) {
        repeat((seconds / frame).toInt()) { advance(frame, state, level) }
    }

    @Test
    fun `starts in the given state with the sweep at 12 o'clock`() {
        val motion = OrbMotion(OrbState.Thinking)
        assertEquals(1f, motion.weight(OrbState.Thinking), 0f)
        assertEquals(0f, motion.weight(OrbState.Idle), 0f)
        assertEquals(OrbMotion.START_ANGLE, motion.sweepAngle, 0f)
    }

    @Test
    fun `idle sweeps a full turn in about six seconds, anticlockwise`() {
        val motion = OrbMotion(OrbState.Idle)
        assertEquals(60f, motion.sweepSpeed, 0.01f)
        motion.advance(0.05f, OrbState.Idle, 0f)
        assertEquals(OrbMotion.START_ANGLE - 3f, motion.sweepAngle, 0.01f)
    }

    @Test
    fun `each state sweeps faster than idle, thinking fastest`() {
        val speeds = OrbState.entries.associateWith { OrbMotion(it).sweepSpeed }
        assertEquals(360f / 6f, speeds.getValue(OrbState.Idle), 0.01f)
        assertTrue(speeds.getValue(OrbState.Listening) > speeds.getValue(OrbState.Idle))
        assertTrue(speeds.getValue(OrbState.Speaking) > speeds.getValue(OrbState.Idle))
        assertEquals(speeds.values.max(), speeds.getValue(OrbState.Thinking), 0f)
    }

    @Test
    fun `changing state blends without the sweep jumping`() {
        val motion = OrbMotion(OrbState.Idle)
        motion.run(1f, OrbState.Idle)
        var previous = motion.sweepAngle
        val maxStep = 300f * frame + 0.01f
        repeat(120) {
            motion.advance(frame, OrbState.Thinking, 0f)
            val step = OrbMotion.angularDistance(previous, motion.sweepAngle)
            assertTrue("sweep jumped $step°", step <= maxStep)
            previous = motion.sweepAngle
        }
        assertTrue(motion.weight(OrbState.Thinking) > 0.95f)
        assertTrue(motion.weight(OrbState.Idle) < 0.05f)
    }

    @Test
    fun `the blend is gradual, not a cut`() {
        val motion = OrbMotion(OrbState.Idle)
        motion.advance(frame, OrbState.Speaking, 0f)
        val w = motion.weight(OrbState.Speaking)
        assertTrue("weight after one frame was $w", w > 0f && w < 0.2f)
    }

    @Test
    fun `weights always add up to one`() {
        val motion = OrbMotion(OrbState.Idle)
        listOf(OrbState.Listening, OrbState.Thinking, OrbState.Speaking, OrbState.Idle).forEach { s ->
            motion.run(0.3f, s)
            val sum = OrbState.entries.sumOf { motion.weight(it).toDouble() }
            assertEquals(1.0, sum, 1e-4)
        }
    }

    @Test
    fun `listening core grows with the mic level`() {
        val quiet = OrbMotion(OrbState.Listening).apply { run(1f, OrbState.Listening, 0f) }
        val loud = OrbMotion(OrbState.Listening).apply { run(1f, OrbState.Listening, 1f) }
        assertTrue(loud.coreScale > quiet.coreScale + 0.25f)
        assertTrue(loud.coreBrightness > quiet.coreBrightness)
    }

    @Test
    fun `level rises fast and falls slower`() {
        val motion = OrbMotion(OrbState.Speaking)
        motion.run(0.1f, OrbState.Speaking, 1f)
        assertTrue("attack too slow: ${motion.level}", motion.level > 0.85f)
        motion.run(0.1f, OrbState.Speaking, 0f)
        assertTrue("release too fast: ${motion.level}", motion.level > 0.4f)
        motion.run(1f, OrbState.Speaking, 0f)
        assertEquals(0f, motion.level, 0.01f)
    }

    @Test
    fun `level is clamped to 0 to 1`() {
        val motion = OrbMotion(OrbState.Listening, level = 3f)
        assertEquals(1f, motion.level, 0f)
        motion.run(1f, OrbState.Listening, -2f)
        assertEquals(0f, motion.level, 0.01f)
    }

    @Test
    fun `speaking lights the ticks by the sweep edge with the level`() {
        val motion = OrbMotion(OrbState.Speaking)
        motion.run(0.5f, OrbState.Speaking, 1f)
        val edge = motion.sweepAngle
        assertTrue(motion.tickHighlight(edge) > 0.8f)
        assertEquals(0f, motion.tickHighlight(edge + 180f), 0f)

        motion.run(1f, OrbState.Speaking, 0f)
        assertEquals(0f, motion.tickHighlight(motion.sweepAngle), 0.02f)
    }

    @Test
    fun `thinking shimmers the ticks and idle doesn't`() {
        val thinking = OrbMotion(OrbState.Thinking).apply { run(0.5f, OrbState.Thinking) }
        val lit = (0 until 72).map { thinking.tickHighlight(it * 5f) }
        assertTrue(lit.max() > 0.7f)
        assertTrue("shimmer should vary round the ring", lit.max() - lit.min() > 0.5f)

        val idle = OrbMotion(OrbState.Idle).apply { run(0.5f, OrbState.Idle) }
        assertTrue((0 until 72).all { idle.tickHighlight(it * 5f) == 0f })
    }

    @Test
    fun `idle core breathes gently`() {
        val motion = OrbMotion(OrbState.Idle)
        val scales = (0 until 240).map {
            motion.advance(frame, OrbState.Idle, 0f)
            motion.coreScale
        }
        assertTrue(scales.max() - scales.min() in 0.05f..0.1f)
    }

    @Test
    fun `a long gap moves the animation by one short step only`() {
        val motion = OrbMotion(OrbState.Thinking)
        motion.advance(30f, OrbState.Thinking, 0f)
        assertEquals(OrbMotion.START_ANGLE - 300f * OrbMotion.MAX_STEP, motion.sweepAngle, 0.01f)
    }

    @Test
    fun `time loops without disturbing the sweep`() {
        val motion = OrbMotion(OrbState.Idle)
        motion.run(OrbMotion.TIME_LOOP + 1f, OrbState.Idle)
        assertTrue(motion.time < OrbMotion.TIME_LOOP)
        assertTrue(motion.sweepAngle in 0f..360f)
    }

    @Test
    fun `reduced motion keeps everything still but the core brightness`() {
        val motion = OrbMotion(OrbState.Idle)
        motion.run(0.5f, OrbState.Idle)
        val angle = motion.sweepAngle

        motion.settle(OrbState.Listening, 0f)
        val quiet = motion.coreBrightness
        motion.settle(OrbState.Listening, 1f)
        assertEquals(angle, motion.sweepAngle, 0f)
        assertEquals(1f, motion.coreScale, 0f)
        assertTrue(motion.coreBrightness > quiet)
        assertEquals(1f, motion.weight(OrbState.Listening), 0f)

        motion.settle(OrbState.Thinking, 0f)
        assertTrue((0 until 72).all { motion.tickHighlight(it * 5f) == 0f })
    }

    @Test
    fun `angles wrap and distances are the short way round`() {
        assertEquals(350f, OrbMotion.wrap(-10f), 0f)
        assertEquals(10f, OrbMotion.wrap(370f), 0f)
        assertEquals(20f, OrbMotion.angularDistance(350f, 10f), 0f)
        assertEquals(180f, OrbMotion.angularDistance(0f, 180f), 0f)
    }
}
