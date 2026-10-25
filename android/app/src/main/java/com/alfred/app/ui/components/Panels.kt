package com.alfred.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.Text
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.alfred.app.ui.theme.AlfredTheme
import com.alfred.app.ui.theme.ChamferShape
import com.alfred.app.ui.theme.Corner
import com.alfred.app.ui.theme.DiagonalCorners

/** Small square indicator and a mono label, e.g. "REC · 00:04" or "ALFRED · TRANSMITTING". */
@Composable
fun StatusPill(
    label: String,
    modifier: Modifier = Modifier,
    indicatorColor: Color = AlfredTheme.colors.accent,
) {
    val colors = AlfredTheme.colors
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        modifier = modifier
            .background(colors.surface)
            .padding(horizontal = AlfredTheme.spacing.l, vertical = 9.dp),
    ) {
        Box(
            Modifier
                .size(7.dp)
                .background(indicatorColor),
        )
        Text(label.uppercase(), style = AlfredTheme.typography.label, color = colors.textPrimary)
    }
}

/**
 * A chamfered tile with a mono label above a value, e.g. PROTOCOL / CLASSIC. With [onClick] it
 * is a button; [valueColor] can be the accent (e.g. an ETA).
 */
@Composable
fun StatTile(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = AlfredTheme.colors.textPrimary,
    corners: Set<Corner> = setOf(Corner.BottomEnd),
    horizontalAlignment: Alignment.Horizontal = Alignment.CenterHorizontally,
    onClick: (() -> Unit)? = null,
) {
    val colors = AlfredTheme.colors
    val body: @Composable (Color?) -> Unit = { tint ->
        Column(
            horizontalAlignment = horizontalAlignment,
            verticalArrangement = Arrangement.spacedBy(2.dp),
            modifier = Modifier.padding(horizontal = AlfredTheme.spacing.l, vertical = 10.dp),
        ) {
            Text(label.uppercase(), style = AlfredTheme.typography.label, color = colors.textSecondary)
            Text(value.uppercase(), style = AlfredTheme.typography.title, color = tint ?: valueColor)
        }
    }
    val shape = ChamferShape(10.dp, corners)
    if (onClick == null) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = modifier
                .clip(shape)
                .background(colors.surface),
        ) { body(null) }
    } else {
        ShapedButton(
            shape = shape,
            onClick = onClick,
            colors = { pressed, enabled ->
                ButtonColors(
                    container = if (pressed) colors.surfacePressed else colors.surface,
                    content = if (enabled) valueColor else colors.textDim,
                    border = null,
                )
            },
            modifier = modifier,
        ) { contentColor -> body(contentColor) }
    }
}

/**
 * A chamfered list card: code label · title · description, an optional [trailing] slot (e.g. a
 * preview play button), and an accent rail on the left while [selected].
 */
@Composable
fun SelectableCard(
    code: String,
    title: String,
    description: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    trailing: (@Composable () -> Unit)? = null,
) {
    val colors = AlfredTheme.colors
    val spacing = AlfredTheme.spacing
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .height(IntrinsicSize.Min)
            .heightIn(min = 64.dp)
            .clip(ChamferShape(10.dp, DiagonalCorners))
            .background(if (pressed) colors.surfacePressed else colors.surface)
            .selectable(
                selected = selected,
                interactionSource = source,
                indication = ripple(color = colors.accent),
                enabled = enabled,
                role = Role.RadioButton,
                onClick = onClick,
            ),
    ) {
        Box(
            Modifier
                .width(3.dp)
                .fillMaxHeight()
                .background(if (selected) colors.accent else Color.Transparent),
        )
        Text(
            code.uppercase(),
            style = AlfredTheme.typography.label.copy(letterSpacing = AlfredTheme.typography.label.letterSpacing / 3),
            color = if (selected) colors.accent else colors.textSecondary,
            modifier = Modifier
                .padding(start = spacing.l)
                .width(52.dp),
        )
        Column(
            verticalArrangement = Arrangement.spacedBy(2.dp),
            modifier = Modifier
                .weight(1f)
                .padding(vertical = spacing.m, horizontal = spacing.m),
        ) {
            Text(title.uppercase(), style = AlfredTheme.typography.title, color = colors.textPrimary)
            Text(
                description,
                style = AlfredTheme.typography.bodySmall,
                color = if (enabled) colors.textSecondary else colors.textDim,
            )
        }
        trailing?.let {
            Box(Modifier.padding(end = spacing.m)) { it() }
        }
    }
}
