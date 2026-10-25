package com.alfred.app.data.voice

import com.alfred.app.testing.FakeDeviceConnection
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LinkHoldsTest {

    private val connection = FakeDeviceConnection()
    private val links = LinkHolds(connection)

    @Test
    fun `socket runs while anyone holds it`() {
        links.hold(LinkHolds.UI)
        assertTrue(connection.started)
        links.hold(LinkHolds.WAKE_WORD)

        assertFalse(links.release(LinkHolds.UI))
        assertTrue(connection.started)
        assertTrue(links.release(LinkHolds.WAKE_WORD))
        assertFalse(connection.started)
    }

    @Test
    fun `releasing twice or without holding does nothing`() {
        links.hold(LinkHolds.UI)
        assertFalse(links.release(LinkHolds.WAKE_WORD))
        assertTrue(connection.started)
        assertTrue(links.release(LinkHolds.UI))
        assertFalse(links.release(LinkHolds.UI))
        assertFalse(links.isHeld(LinkHolds.UI))
    }
}
