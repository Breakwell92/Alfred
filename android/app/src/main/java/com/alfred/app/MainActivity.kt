package com.alfred.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.alfred.app.data.voice.DeviceConnection
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.ui.AlfredApp
import com.alfred.app.ui.theme.AlfredTheme
import org.koin.android.ext.android.inject

class MainActivity : ComponentActivity() {
    private val connection: DeviceConnection by inject()
    private val voice: VoiceController by inject()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Alfred is dark only: light system-bar icons whatever the phone's theme.
        val bars = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
        enableEdgeToEdge(statusBarStyle = bars, navigationBarStyle = bars)
        setContent {
            AlfredTheme {
                AlfredApp()
            }
        }
    }

    // The voice socket is open while the app is on screen. The wake-word service (STU-68)
    // will hold it open in the background.
    override fun onStart() {
        super.onStart()
        connection.start()
    }

    override fun onStop() {
        // A rotation stops and restarts the activity; keep the turn and socket through it.
        if (!isChangingConfigurations) {
            voice.cancel()
            connection.stop()
        }
        super.onStop()
    }
}
