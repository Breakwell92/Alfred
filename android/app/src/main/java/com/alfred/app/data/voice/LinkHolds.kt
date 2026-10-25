package com.alfred.app.data.voice

/**
 * Who needs the device socket open: the app while it's on screen ([UI]) and the wake word
 * service while it runs ([WAKE_WORD]). The socket starts with the first holder and stops when
 * the last one lets go, so leaving the app doesn't cut off a wake word turn.
 */
class LinkHolds(private val connection: DeviceConnection) {
    private val holders = mutableSetOf<String>()

    @Synchronized
    fun hold(owner: String) {
        holders += owner
        connection.start()
    }

    /** True if that was the last holder and the socket has been stopped. */
    @Synchronized
    fun release(owner: String): Boolean {
        if (!holders.remove(owner) || holders.isNotEmpty()) return false
        connection.stop()
        return true
    }

    @Synchronized
    fun isHeld(owner: String): Boolean = owner in holders

    companion object {
        const val UI = "ui"
        const val WAKE_WORD = "wake_word"
    }
}
