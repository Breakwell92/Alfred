package com.alfred.app.ui.home

import com.alfred.app.R
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.ui.orb.OrbState
import java.time.LocalTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeStateTest {

    private val up = LinkState.Connected("s1", voice = true)
    private val retrying = LinkState.Waiting("connection lost", retryInMillis = 2_000)

    @Test
    fun `secure only when the server and the voice link are both up`() {
        assertEquals(
            StatusLine(R.string.status_secure, StatusTone.Secure),
            statusLine(ConnectionStatus.Connected, up),
        )
        for (link in listOf(LinkState.Connecting, retrying, LinkState.Offline)) {
            assertEquals(
                StatusLine(R.string.status_reconnecting, StatusTone.Pending),
                statusLine(ConnectionStatus.Connected, link),
            )
        }
    }

    @Test
    fun `first check shows connecting unless the link is already up`() {
        assertEquals(
            StatusLine(R.string.status_connecting, StatusTone.Pending),
            statusLine(ConnectionStatus.Checking, LinkState.Connecting),
        )
        assertEquals(StatusTone.Secure, statusLine(ConnectionStatus.Checking, up).tone)
    }

    @Test
    fun `server problems are down whatever the link says`() {
        for (status in listOf(ConnectionStatus.Unreachable("timeout"), ConnectionStatus.ServerError(500))) {
            assertEquals(StatusLine(R.string.status_line_down, StatusTone.Down), statusLine(status, up))
        }
        for (status in listOf(ConnectionStatus.Unauthorised, ConnectionStatus.InvalidToken)) {
            assertEquals(StatusLine(R.string.status_unauthorised, StatusTone.Down), statusLine(status, retrying))
        }
        assertEquals(
            StatusLine(R.string.status_not_set_up, StatusTone.Down),
            statusLine(ConnectionStatus.NotConfigured, LinkState.NotConfigured),
        )
    }

    @Test
    fun `a refused socket is down even if http answers`() {
        assertEquals(
            StatusLine(R.string.status_line_down, StatusTone.Down),
            statusLine(ConnectionStatus.Connected, LinkState.Rejected("protocol version")),
        )
    }

    @Test
    fun `greeting changes on the hour boundaries`() {
        assertEquals(R.string.greeting_evening, greetingFor(4))
        assertEquals(R.string.greeting_morning, greetingFor(5))
        assertEquals(R.string.greeting_morning, greetingFor(11))
        assertEquals(R.string.greeting_afternoon, greetingFor(12))
        assertEquals(R.string.greeting_afternoon, greetingFor(17))
        assertEquals(R.string.greeting_evening, greetingFor(18))
        assertEquals(R.string.greeting_evening, greetingFor(0))
    }

    @Test
    fun `clock is 24 hour with leading zeros`() {
        assertEquals("02:14", formatClock(LocalTime.of(2, 14, 59)))
        assertEquals("23:05", formatClock(LocalTime.of(23, 5)))
    }

    @Test
    fun `orb follows the voice phase`() {
        assertEquals(OrbState.Idle, orbStateFor(VoicePhase.Idle))
        assertEquals(OrbState.Listening, orbStateFor(VoicePhase.Listening))
        assertEquals(OrbState.Thinking, orbStateFor(VoicePhase.Thinking))
        assertEquals(OrbState.Speaking, orbStateFor(VoicePhase.Speaking))
    }

    @Test
    fun `mic starts a turn only over a voice link, but always stops one`() {
        assertTrue(micEnabled(VoicePhase.Idle, up))
        assertFalse(micEnabled(VoicePhase.Idle, retrying))
        assertFalse(micEnabled(VoicePhase.Idle, LinkState.Connected("s1", voice = false)))
        assertTrue(micEnabled(VoicePhase.Speaking, retrying))
        assertTrue(micEnabled(VoicePhase.Listening, LinkState.Offline))
    }
}
