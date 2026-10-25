package com.alfred.app.ui.settings

import com.alfred.app.data.reliability.ServiceHealth
import java.time.ZoneOffset
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Test

class BackgroundHealthTest {

    private val zone = ZoneOffset.UTC
    private fun at(day: Int, hour: Int, minute: Int) =
        ZonedDateTime.of(2026, 10, day, hour, minute, 0, 0, zone).toInstant().toEpochMilli()

    private val now = at(25, 18, 0) // a Sunday

    @Test
    fun `a healthy listener`() {
        val health = ServiceHealth(listeningSince = at(25, 9, 12), cleanStop = false)

        assertEquals(
            listOf(
                "Listening since 09:12",
                "No restarts after Android stopped it",
                "Reconnected to the server 0×",
            ),
            healthLines(health, running = true, now = now, zone = zone),
        )
    }

    @Test
    fun `restarts and reconnects, with older times dated`() {
        val health = ServiceHealth(
            listeningSince = at(23, 7, 5),
            cleanStop = false,
            restartCount = 2,
            lastRestartAt = at(23, 3, 40),
            lastGapMillis = 12 * 60_000L,
            reconnectCount = 5,
        )

        assertEquals(
            listOf(
                "Listening since Fri 07:05",
                "Restarted 2× · last at Fri 03:40 after a 12 min gap",
                "Reconnected to the server 5×",
            ),
            healthLines(health, running = true, now = now, zone = zone),
        )
    }

    @Test
    fun `gaps of hours, and a date more than a week back`() {
        val health = ServiceHealth(restartCount = 1, lastRestartAt = at(3, 22, 0), lastGapMillis = 135 * 60_000L)

        assertEquals(
            "Restarted 1× · last at 3 Oct 22:00 after a 2 h 15 min gap",
            healthLines(health, running = false, now = now, zone = zone)[0],
        )
    }

    @Test
    fun `no listening line when it isn't running`() {
        val health = ServiceHealth(listeningSince = at(25, 9, 0), cleanStop = false)

        assertEquals(2, healthLines(health, running = false, now = now, zone = zone).size)
    }

    @Test
    fun `links the maker's guide on dontkillmyapp`() {
        assertEquals("https://dontkillmyapp.com/samsung", dontKillMyAppUrl("samsung"))
        assertEquals("https://dontkillmyapp.com/google", dontKillMyAppUrl("Google"))
        assertEquals("https://dontkillmyapp.com/xiaomi", dontKillMyAppUrl(" Redmi "))
        assertEquals("https://dontkillmyapp.com/nokia", dontKillMyAppUrl("HMD Global"))
        assertEquals("https://dontkillmyapp.com/", dontKillMyAppUrl("Fairphone"))
    }
}
