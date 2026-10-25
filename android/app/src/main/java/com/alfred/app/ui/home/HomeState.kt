package com.alfred.app.ui.home

import androidx.annotation.StringRes
import com.alfred.app.R
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.ui.orb.OrbState
import java.time.LocalTime
import java.util.Locale

/** How the status line's square and label are coloured. */
enum class StatusTone {
    /** Accent: everything's up. */
    Secure,

    /** Amber: on the way up, or the voice link is retrying. */
    Pending,

    /** Red: down, refused or not set up. */
    Down,
}

data class StatusLine(@StringRes val label: Int, val tone: StatusTone)

/**
 * Home's status line from the HTTP check (polled every 10 s) and the voice socket (which notices
 * a drop at once). Only when both are up is the line secure.
 */
fun statusLine(status: ConnectionStatus, link: LinkState): StatusLine = when (status) {
    ConnectionStatus.NotConfigured -> StatusLine(R.string.status_not_set_up, StatusTone.Down)
    ConnectionStatus.Unauthorised, ConnectionStatus.InvalidToken ->
        StatusLine(R.string.status_unauthorised, StatusTone.Down)
    is ConnectionStatus.Unreachable, is ConnectionStatus.ServerError ->
        StatusLine(R.string.status_line_down, StatusTone.Down)
    ConnectionStatus.Checking -> when (link) {
        is LinkState.Connected -> StatusLine(R.string.status_secure, StatusTone.Secure)
        is LinkState.Rejected -> StatusLine(R.string.status_line_down, StatusTone.Down)
        else -> StatusLine(R.string.status_connecting, StatusTone.Pending)
    }
    ConnectionStatus.Connected -> when (link) {
        is LinkState.Connected -> StatusLine(R.string.status_secure, StatusTone.Secure)
        // The server answers HTTP but refuses the socket (e.g. a protocol mismatch).
        is LinkState.Rejected -> StatusLine(R.string.status_line_down, StatusTone.Down)
        else -> StatusLine(R.string.status_reconnecting, StatusTone.Pending)
    }
}

/** "GOOD MORNING" from 05:00, "AFTERNOON" from 12:00, "EVENING" from 18:00 through the night. */
@StringRes
fun greetingFor(hour: Int): Int = when (hour) {
    in 5..11 -> R.string.greeting_morning
    in 12..17 -> R.string.greeting_afternoon
    else -> R.string.greeting_evening
}

/** The status line's clock, 24 h: "02:14". */
fun formatClock(time: LocalTime): String =
    String.format(Locale.ROOT, "%02d:%02d", time.hour, time.minute)

fun orbStateFor(phase: VoicePhase): OrbState = when (phase) {
    VoicePhase.Idle -> OrbState.Idle
    VoicePhase.Listening -> OrbState.Listening
    VoicePhase.Thinking -> OrbState.Thinking
    VoicePhase.Speaking -> OrbState.Speaking
}

/**
 * The mic can start a turn only over a voice-enabled link. Mid-turn it always works, since a
 * tap then sends or stops.
 */
fun micEnabled(phase: VoicePhase, link: LinkState): Boolean =
    phase != VoicePhase.Idle || (link is LinkState.Connected && link.voice)
