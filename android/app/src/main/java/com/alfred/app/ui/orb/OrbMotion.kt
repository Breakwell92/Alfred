package com.alfred.app.ui.orb

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sin

/** What the orb shows (ADR 0020). */
enum class OrbState {
    /** Waiting: slow sweep, gentle breathing. */
    Idle,

    /** Hearing Dan: faster sweep, core follows the mic level. */
    Listening,

    /** Waiting on the server: fast sweep, steady pulse, shimmering ticks. */
    Thinking,

    /** Replying: core and the ticks by the sweep follow the playback level. */
    Speaking,
}

/**
 * The orb's animation model: advanced once a frame by [AlfredOrb] and read by its draw code.
 * Plain Kotlin, so it can be unit-tested, and it allocates nothing after construction.
 *
 * Every state has a weight that eases between 0 and 1, and each animated value is the
 * weighted mix of the states' values, so changing state blends rather than jumps. The sweep
 * angle is integrated from a blended speed, so it never jumps either.
 */
class OrbMotion(state: OrbState = OrbState.Idle, level: Float = 0f) {
    private val weights = FloatArray(STATES.size).also { it[state.ordinal] = 1f }

    /**
     * Seconds of animation, looping every [TIME_LOOP] so precision holds however long the orb
     * runs; drives breathing, pulses and shimmer.
     */
    var time: Float = 0f
        private set

    /**
     * The sweep's sharp leading edge in degrees, clockwise from 3 o'clock (Compose's drawing
     * convention), in `[0, 360)`. It decreases: the sweep turns anticlockwise with its glow
     * trailing behind the edge, as in the design.
     */
    var sweepAngle: Float = START_ANGLE
        private set

    /** The input level after attack/release smoothing, 0 to 1. */
    var level: Float = level.coerceIn(0f, 1f)
        private set

    /** Core radius multiplier. */
    var coreScale: Float = 1f
        private set

    /** Core opacity, 0 to 1. */
    var coreBrightness: Float = 1f
        private set

    /** True while the system's "remove animations" setting is on: nothing moves. */
    var reducedMotion: Boolean = false
        private set

    // How much each tick effect is showing, 0 to 1.
    private var shimmer = 0f
    private var tickPulse = 0f

    init {
        update()
    }

    /** Degrees per second the sweep is turning right now. */
    val sweepSpeed: Float
        get() = mix(SWEEP_SPEED)

    /** The weight of [state] in the current blend, 0 to 1. */
    fun weight(state: OrbState): Float = weights[state.ordinal]

    /**
     * Moves the animation on by [seconds], heading for [state] with the raw input [level]. A
     * long gap (the app was in the background) counts as one short frame, so nothing leaps.
     */
    fun advance(seconds: Float, state: OrbState, level: Float) {
        reducedMotion = false
        val dt = seconds.coerceIn(0f, MAX_STEP)
        val blend = 1f - exp(-dt / BLEND_TIME)
        for (i in weights.indices) {
            val target = if (i == state.ordinal) 1f else 0f
            weights[i] += (target - weights[i]) * blend
        }
        val raw = level.coerceIn(0f, 1f)
        val tau = if (raw > this.level) LEVEL_ATTACK else LEVEL_RELEASE
        this.level += (raw - this.level) * (1f - exp(-dt / tau))
        val speed = sweepSpeed
        time = (time + dt) % TIME_LOOP
        sweepAngle = wrap(sweepAngle - speed * dt)
        update()
    }

    /**
     * Jumps straight to [state] and [level] with nothing moving, for the system's "remove
     * animations" setting: the sweep stays where it is and only the core's brightness shows
     * the state and level.
     */
    fun settle(state: OrbState, level: Float) {
        reducedMotion = true
        weights.fill(0f)
        weights[state.ordinal] = 1f
        this.level = level.coerceIn(0f, 1f)
        update()
    }

    /**
     * How lit the tick at [angle] (degrees, same convention as [sweepAngle]) is, 0 to 1:
     * shimmer while thinking, and a level-driven glow near the sweep's edge while speaking.
     */
    fun tickHighlight(angle: Float): Float {
        if (reducedMotion) return 0f
        var lit = 0f
        if (shimmer > 0f) {
            // A bright band running clockwise round the ring, over a fast flicker.
            val band = falloff(angularDistance(angle, wrap(time * SHIMMER_SPEED)), SHIMMER_WIDTH)
            val flicker = 0.5f + 0.5f * sin(angle * 0.7f + TWO_PI * time * FLICKER_RATE)
            lit += shimmer * (0.75f * band + 0.25f * flicker)
        }
        if (tickPulse > 0f) {
            lit += tickPulse * falloff(angularDistance(angle, sweepAngle), PULSE_WIDTH)
        }
        return lit.coerceIn(0f, 1f)
    }

    private fun update() {
        val still = reducedMotion
        val breath = if (still) 0f else sin(TWO_PI * time / BREATH_PERIOD)
        val pulse = if (still) 0.5f else 0.5f + 0.5f * sin(TWO_PI * time * PULSE_RATE)
        val idle = weights[OrbState.Idle.ordinal]
        val listening = weights[OrbState.Listening.ordinal]
        val thinking = weights[OrbState.Thinking.ordinal]
        val speaking = weights[OrbState.Speaking.ordinal]

        coreScale = if (still) {
            1f
        } else {
            idle * (1f + 0.04f * breath) +
                listening * (1f + 0.02f * breath + 0.35f * level) +
                thinking * (0.98f + 0.1f * pulse) +
                speaking * (1f + 0.3f * level)
        }
        coreBrightness = idle * (0.85f + 0.1f * breath) +
            listening * (0.7f + 0.3f * level) +
            thinking * (0.75f + 0.25f * pulse) +
            speaking * (0.7f + 0.3f * level)
        shimmer = if (still) 0f else thinking
        tickPulse = if (still) 0f else speaking * level
    }

    private fun mix(values: FloatArray): Float {
        var sum = 0f
        for (i in weights.indices) sum += weights[i] * values[i]
        return sum
    }

    companion object {
        private val STATES = OrbState.entries

        /** 12 o'clock, where the design shows the edge. */
        const val START_ANGLE = 270f

        /** Sweep speeds in degrees per second, by [OrbState] ordinal (6, 3, 1.2, 2.5 s a turn). */
        private val SWEEP_SPEED = floatArrayOf(60f, 120f, 300f, 144f)

        /** Time constant of the blend between states, in seconds (~95 % after 0.75 s). */
        const val BLEND_TIME = 0.25f

        /** The longest step one frame may take, in seconds. */
        const val MAX_STEP = 0.1f

        private const val LEVEL_ATTACK = 0.04f
        private const val LEVEL_RELEASE = 0.18f
        /**
         * Every time-driven effect repeats a whole number of times in this many seconds, so
         * wrapping [time] never makes anything jump.
         */
        const val TIME_LOOP = 12f

        // Breath every 4 s, pulse every 0.8 s, shimmer round once every 1.5 s, flicker 2.25 Hz.
        private const val BREATH_PERIOD = 4f
        private const val PULSE_RATE = 1.25f
        private const val SHIMMER_SPEED = 240f
        private const val SHIMMER_WIDTH = 35f
        private const val FLICKER_RATE = 2.25f
        private const val PULSE_WIDTH = 50f
        private const val TWO_PI = (2 * PI).toFloat()

        internal fun wrap(degrees: Float): Float {
            val d = degrees % 360f
            return if (d < 0f) d + 360f else d
        }

        /** Shortest distance between two angles, 0 to 180. */
        internal fun angularDistance(a: Float, b: Float): Float {
            val d = abs(wrap(a) - wrap(b))
            return if (d > 180f) 360f - d else d
        }

        private fun falloff(distance: Float, width: Float): Float =
            if (distance >= width) 0f else 1f - distance / width
    }
}
