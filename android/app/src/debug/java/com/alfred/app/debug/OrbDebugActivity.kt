package com.alfred.app.debug

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.components.StatTile
import com.alfred.app.ui.orb.AlfredOrb
import com.alfred.app.ui.orb.OrbSize
import com.alfred.app.ui.orb.OrbState
import com.alfred.app.ui.theme.AlfredTheme

/**
 * Debug builds only: the orb driven by [fakeSpeechLevels], with every state, both sizes and the
 * guides switchable (STU-113). Open it with
 * `adb shell am start -n com.alfred.app/com.alfred.app.debug.OrbDebugActivity`.
 */
class OrbDebugActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bars = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
        enableEdgeToEdge(statusBarStyle = bars, navigationBarStyle = bars)
        setContent {
            AlfredTheme {
                GridBackground(Modifier.fillMaxSize()) { OrbDebugScreen() }
            }
        }
    }
}

@Composable
private fun OrbDebugScreen() {
    var state by remember { mutableStateOf(OrbState.Listening) }
    var size by remember { mutableStateOf(OrbSize.Large) }
    var guides by remember { mutableStateOf(true) }
    var speech by remember { mutableStateOf(true) }
    var level by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(speech) {
        if (speech) fakeSpeechLevels().collect { level = it } else level = 0f
    }

    val colors = AlfredTheme.colors
    val spacing = AlfredTheme.spacing
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(spacing.m),
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .padding(spacing.gutter),
    ) {
        Text("ORB · DEBUG", style = AlfredTheme.typography.label, color = colors.accent)
        Spacer(Modifier.weight(1f))
        AlfredOrb(state = state, level = level, size = size, showGuides = guides, channelLabel = "CH-01")
        Spacer(Modifier.weight(1f))
        Text(
            "LEVEL %.2f".format(level),
            style = AlfredTheme.typography.label,
            color = colors.textSecondary,
        )
        OrbState.entries.chunked(2).forEach { pair ->
            TileRow {
                pair.forEach { s -> Toggle("STATE", s.name, selected = state == s, onClick = { state = s }) }
            }
        }
        TileRow {
            Toggle("SIZE", size.name, selected = true, onClick = {
                size = if (size == OrbSize.Large) OrbSize.Small else OrbSize.Large
            })
            Toggle("GUIDES", if (guides) "ON" else "OFF", selected = guides, onClick = { guides = !guides })
            Toggle("SPEECH", if (speech) "FAKE" else "OFF", selected = speech, onClick = { speech = !speech })
        }
    }
}

@Composable
private fun TileRow(content: @Composable RowScope.() -> Unit) = Row(
    horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.s),
    modifier = Modifier.fillMaxWidth(),
    content = content,
)

@Composable
private fun RowScope.Toggle(label: String, value: String, selected: Boolean, onClick: () -> Unit) = StatTile(
    label = label,
    value = value.uppercase(),
    valueColor = if (selected) AlfredTheme.colors.accent else AlfredTheme.colors.textSecondary,
    onClick = onClick,
    modifier = Modifier.weight(1f),
)
