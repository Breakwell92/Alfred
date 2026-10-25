package com.alfred.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.DesktopWindows
import androidx.compose.material.icons.outlined.GpsFixed
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.alfred.app.ui.theme.AlfredTheme
import com.alfred.app.ui.theme.Corner

// The design system, one preview per component (STU-112). Screens are built from these.

@Composable
private fun Swatch(content: @Composable () -> Unit) {
    AlfredTheme {
        GridBackground {
            Column(
                verticalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.l),
                modifier = Modifier.padding(AlfredTheme.spacing.gutter),
            ) { content() }
        }
    }
}

@Preview(name = "Type", widthDp = 360)
@Composable
private fun TypePreview() = Swatch {
    val type = AlfredTheme.typography
    val colors = AlfredTheme.colors
    Text("ALFRED", style = type.wordmark, color = colors.textPrimary)
    Text("BATCAVE UPLINK", style = type.label, color = colors.accent)
    Text("WHAT DO YOU NEED?", style = type.displayL, color = colors.textPrimary)
    Text("HOW SHALL I SOUND, SIR?", style = type.displayM, color = colors.textPrimary)
    Text("CLASSIC", style = type.title, color = colors.textPrimary)
    Text("SECURE LINE · ENCRYPTED", style = type.label, color = colors.textSecondary)
    Text("Alfred, where's Penguin's shipment coming in tonight?", style = type.body, color = colors.textPrimary)
    Text("Dry wit, impeccable manners", style = type.bodySmall, color = colors.textSecondary)
    Text("ENGAGE CLASSIC", style = type.button, color = colors.accent)
}

@Preview(name = "Grid background", widthDp = 360, heightDp = 200)
@Composable
private fun GridBackgroundPreview() = Swatch {}

@Preview(name = "Chamfer icon buttons", widthDp = 360)
@Composable
private fun ChamferIconButtonPreview() = Swatch {
    Row(horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.m)) {
        ChamferIconButton(Icons.Filled.Menu, "Menu", onClick = {})
        ChamferIconButton(Icons.Filled.Close, "Close", onClick = {})
        ChamferIconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", onClick = {})
        ChamferIconButton(
            Icons.Outlined.GpsFixed,
            "Crosshair",
            onClick = {},
            corners = setOf(Corner.TopEnd, Corner.BottomStart),
        )
        ChamferIconButton(Icons.Outlined.DesktopWindows, "Screen", onClick = {})
    }
    Row(horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.m)) {
        ChamferIconButton(Icons.Filled.MicOff, "Mute", onClick = {})
        ChamferIconButton(Icons.AutoMirrored.Filled.Send, "Send", onClick = {})
        ChamferIconButton(Icons.Filled.MicOff, "Disabled", onClick = {}, enabled = false)
        ChamferIconButton(
            Icons.Filled.PlayArrow,
            "Play",
            onClick = {},
            corners = emptySet(),
            containerColor = AlfredTheme.colors.background,
        )
    }
}

@Preview(name = "Status pills", widthDp = 360)
@Composable
private fun StatusPillPreview() = Swatch {
    StatusPill("REC · 00:04", indicatorColor = AlfredTheme.colors.record)
    StatusPill("Alfred · transmitting")
}

@Preview(name = "Stat tiles", widthDp = 360)
@Composable
private fun StatTilePreview() = Swatch {
    Row(horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.m)) {
        StatTile("Protocol", "Classic", Modifier.weight(1f), onClick = {})
        StatTile(
            "Patrol",
            "Off",
            Modifier.weight(1f),
            corners = setOf(Corner.TopEnd, Corner.BottomStart),
            onClick = {},
        )
    }
    Row(horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.s)) {
        StatTile(
            "Target",
            "Gotham docks · P39",
            Modifier.weight(1f),
            corners = setOf(Corner.TopEnd),
            horizontalAlignment = Alignment.Start,
        )
        StatTile(
            "ETA · Batmobile",
            "14 min",
            Modifier.weight(1f),
            valueColor = AlfredTheme.colors.accent,
            corners = setOf(Corner.TopEnd),
            horizontalAlignment = Alignment.Start,
        )
    }
}

@Preview(name = "Octagon action buttons", widthDp = 360)
@Composable
private fun OctagonActionButtonPreview() = Swatch {
    Row(horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.xl)) {
        OctagonActionButton(Icons.Filled.Mic, "Talk", onClick = {})
        OctagonActionButton(Icons.Filled.Stop, "Send", onClick = {}, outlined = true)
        OctagonActionButton(Icons.Filled.Mic, "Disabled", onClick = {}, enabled = false)
    }
}

@Preview(name = "Primary and hexagon buttons", widthDp = 360)
@Composable
private fun PrimaryButtonPreview() = Swatch {
    PrimaryButton("Engage classic", onClick = {})
    PrimaryButton("Engage classic", onClick = {}, enabled = false)
    HexagonButton("Cut in", onClick = {}, icon = Icons.Filled.Pause, modifier = Modifier.width(200.dp))
}

@Preview(name = "Selectable cards", widthDp = 360)
@Composable
private fun SelectableCardPreview() = Swatch {
    val play: @Composable () -> Unit = {
        ChamferIconButton(
            Icons.Filled.PlayArrow,
            "Preview voice",
            onClick = {},
            corners = emptySet(),
            size = 40.dp,
            containerColor = AlfredTheme.colors.background,
        )
    }
    SelectableCard(
        "P-01",
        "Classic",
        "Dry wit, impeccable manners",
        selected = true,
        onClick = {},
        modifier = Modifier.fillMaxWidth(),
        trailing = play,
    )
    SelectableCard(
        "P-03",
        "Stealth",
        "Barely above a whisper, for rooftops",
        selected = false,
        onClick = {},
        modifier = Modifier.fillMaxWidth(),
        trailing = play,
    )
    SelectableCard(
        "P-04",
        "Decoy",
        "Not available",
        selected = false,
        onClick = {},
        enabled = false,
        modifier = Modifier.fillMaxWidth(),
    )
}
