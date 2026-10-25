package com.alfred.app.data.reliability

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServiceStarterTest {

    @Test
    fun `asks first only from the background on Android 14 and later`() {
        assertTrue(mustAskFirst(sdkInt = 34, inForeground = false))
        assertTrue(mustAskFirst(sdkInt = 37, inForeground = false))
        assertFalse(mustAskFirst(sdkInt = 37, inForeground = true))
        assertFalse(mustAskFirst(sdkInt = 33, inForeground = false))
    }
}
