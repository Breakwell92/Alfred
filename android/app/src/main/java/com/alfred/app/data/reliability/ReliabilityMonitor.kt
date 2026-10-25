package com.alfred.app.data.reliability

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import com.alfred.app.data.voice.DeviceConnection
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** Tells its listener whenever the phone gets a (new) default network. */
fun interface NetworkMonitor {
    fun watch(onAvailable: () -> Unit)
}

class AndroidNetworkMonitor(private val context: Context) : NetworkMonitor {
    override fun watch(onAvailable: () -> Unit) {
        context.getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = onAvailable()
            },
        )
    }
}

/**
 * Started once with the app (ADR 0025): reconnects the socket as soon as a network comes back
 * instead of waiting out the backoff (Doze delays timers, not network callbacks), and counts
 * reconnects for the health indicator.
 */
class ReliabilityMonitor(
    private val connection: DeviceConnection,
    private val recorder: ReliabilityRecorder,
    private val network: NetworkMonitor,
    private val scope: CoroutineScope,
) {
    private var started = false

    @Synchronized
    fun start() {
        if (started) return
        started = true
        network.watch(connection::networkAvailable)
        scope.launch { recorder.countReconnects(connection.state) }
    }
}
