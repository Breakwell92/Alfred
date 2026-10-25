package com.alfred.app.ui.orb

import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.theme.AlfredTheme

// One preview per state and size (STU-113). Run them interactively to see them move.

@Composable
private fun OrbSwatch(state: OrbState, size: OrbSize, level: Float = 0f) {
    AlfredTheme {
        GridBackground {
            AlfredOrb(
                state = state,
                level = level,
                size = size,
                channelLabel = if (size == OrbSize.Large) "CH-01" else null,
                modifier = Modifier.padding(AlfredTheme.spacing.xxl),
            )
        }
    }
}

@Preview(name = "Large · idle")
@Composable
private fun LargeIdlePreview() = OrbSwatch(OrbState.Idle, OrbSize.Large)

@Preview(name = "Large · listening")
@Composable
private fun LargeListeningPreview() = OrbSwatch(OrbState.Listening, OrbSize.Large, level = 0.6f)

@Preview(name = "Large · thinking")
@Composable
private fun LargeThinkingPreview() = OrbSwatch(OrbState.Thinking, OrbSize.Large)

@Preview(name = "Large · speaking")
@Composable
private fun LargeSpeakingPreview() = OrbSwatch(OrbState.Speaking, OrbSize.Large, level = 0.6f)

@Preview(name = "Small · idle")
@Composable
private fun SmallIdlePreview() = OrbSwatch(OrbState.Idle, OrbSize.Small)

@Preview(name = "Small · listening")
@Composable
private fun SmallListeningPreview() = OrbSwatch(OrbState.Listening, OrbSize.Small, level = 0.6f)

@Preview(name = "Small · thinking")
@Composable
private fun SmallThinkingPreview() = OrbSwatch(OrbState.Thinking, OrbSize.Small)

@Preview(name = "Small · speaking")
@Composable
private fun SmallSpeakingPreview() = OrbSwatch(OrbState.Speaking, OrbSize.Small, level = 0.6f)
