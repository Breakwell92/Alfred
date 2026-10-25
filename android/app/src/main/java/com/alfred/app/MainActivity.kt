package com.alfred.app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.alfred.app.data.voice.LinkHolds
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import com.alfred.app.service.WakeWordService
import com.alfred.app.ui.AlfredApp
import com.alfred.app.ui.theme.AlfredTheme
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject

class MainActivity : ComponentActivity() {
    private val links: LinkHolds by inject()
    private val voice: VoiceController by inject()
    private val wakeWordSettings: WakeWordSettingsRepository by inject()

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
        // The wake word service follows the Settings toggle. It can only be started while the
        // app is on screen (Android's rule for mic services); once running it stays running.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                wakeWordSettings.settings.map { it.enabled }.distinctUntilChanged().collect { enabled ->
                    when {
                        enabled && hasMic() -> WakeWordService.start(this@MainActivity)
                        !enabled -> WakeWordService.stop(this@MainActivity)
                    }
                }
            }
        }
    }

    // The voice socket is open while the app is on screen, and while the wake word service runs.
    override fun onStart() {
        super.onStart()
        links.hold(LinkHolds.UI)
    }

    override fun onStop() {
        // A rotation stops and restarts the activity; keep the turn and socket through it. A turn
        // carries on in the background only if the wake word service is keeping the socket open.
        if (!isChangingConfigurations && links.release(LinkHolds.UI)) voice.cancel()
        super.onStop()
    }

    private fun hasMic() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
}
