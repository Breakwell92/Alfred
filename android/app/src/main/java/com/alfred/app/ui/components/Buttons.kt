package com.alfred.app.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.PressGestureScope
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.indication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.alfred.app.ui.theme.AlfredTheme
import com.alfred.app.ui.theme.ChamferShape
import com.alfred.app.ui.theme.Corner
import com.alfred.app.ui.theme.DiagonalCorners
import com.alfred.app.ui.theme.HexagonPillShape
import com.alfred.app.ui.theme.OctagonShape

/** Fill, content and border colours for a shaped button in one state. */
internal data class ButtonColors(val container: Color, val content: Color, val border: Color?)

/**
 * The base of every shaped button: fill, border and ripple are all clipped to [shape], the fill
 * changes while pressed, and a disabled button dims and ignores taps.
 *
 * With [onPress] the button follows the finger instead of waiting for a click: [onPress] runs
 * when it goes down and can wait for the release (`tryAwaitRelease`), e.g. hold to talk.
 * [onClick] is then what accessibility services do. [onDisabledClick] lets a disabled button
 * say why it is disabled when tapped.
 */
@Composable
internal fun ShapedButton(
    shape: Shape,
    onClick: () -> Unit,
    colors: (pressed: Boolean, enabled: Boolean) -> ButtonColors,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    borderWidth: Dp = 1.5.dp,
    interactionSource: MutableInteractionSource? = null,
    onPress: (suspend PressGestureScope.() -> Unit)? = null,
    onDisabledClick: (() -> Unit)? = null,
    content: @Composable (contentColor: Color) -> Unit,
) {
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    val c = colors(pressed, enabled)
    val currentOnPress by rememberUpdatedState(onPress)
    val currentOnClick by rememberUpdatedState(onClick)
    val input = when {
        // Still announced as disabled; the tap only explains why.
        !enabled && onDisabledClick != null -> Modifier
            .semantics { disabled() }
            .clickable(interactionSource = source, indication = null, role = Role.Button, onClick = onDisabledClick)
        onPress != null && enabled -> Modifier
            .indication(source, ripple(color = c.content))
            .semantics {
                role = Role.Button
                onClick(label = null) {
                    currentOnClick()
                    true
                }
            }
            .pointerInput(source) {
                detectTapGestures(
                    onPress = { offset ->
                        val press = PressInteraction.Press(offset)
                        source.tryEmit(press)
                        try {
                            currentOnPress?.invoke(this)
                        } finally {
                            source.tryEmit(PressInteraction.Release(press))
                        }
                    },
                )
            }
        else -> Modifier.clickable(
            interactionSource = source,
            indication = ripple(color = c.content),
            enabled = enabled,
            role = Role.Button,
            onClick = onClick,
        )
    }
    Box(
        contentAlignment = Alignment.Center,
        modifier = modifier
            .clip(shape)
            .background(c.container, shape)
            .then(if (c.border != null) Modifier.border(BorderStroke(borderWidth, c.border), shape) else Modifier)
            .then(input),
    ) {
        content(c.content)
    }
}

@Composable
private fun accentColors(): (Boolean, Boolean) -> ButtonColors {
    val colors = AlfredTheme.colors
    return { pressed, enabled ->
        when {
            !enabled -> ButtonColors(colors.surfaceRaised, colors.textDim, null)
            pressed -> ButtonColors(colors.accentPressed, colors.onAccent, null)
            else -> ButtonColors(colors.accent, colors.onAccent, null)
        }
    }
}

@Composable
private fun surfaceColors(container: Color, tint: Color): (Boolean, Boolean) -> ButtonColors {
    val colors = AlfredTheme.colors
    return { pressed, enabled ->
        when {
            !enabled -> ButtonColors(container, colors.textDim, null)
            pressed -> ButtonColors(colors.surfacePressed, tint, null)
            else -> ButtonColors(container, tint, null)
        }
    }
}

/**
 * Square icon button with chamfered [corners] (menu, close, back, crosshair, screen, mute, send).
 * Pass `emptySet()` for a plain square, e.g. the play button on a persona card.
 */
@Composable
fun ChamferIconButton(
    icon: ImageVector,
    contentDescription: String?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    corners: Set<Corner> = DiagonalCorners,
    size: Dp = 48.dp,
    containerColor: Color = AlfredTheme.colors.surface,
    tint: Color = AlfredTheme.colors.textPrimary,
) {
    ShapedButton(
        shape = ChamferShape(size / 5, corners),
        onClick = onClick,
        colors = surfaceColors(containerColor, tint),
        modifier = modifier.size(size),
        enabled = enabled,
    ) { contentColor ->
        Icon(icon, contentDescription = contentDescription, tint = contentColor, modifier = Modifier.size(size / 2))
    }
}

/**
 * The main action: an accent-filled octagon, or with [outlined] a dark octagon with an accent
 * border (the stop state while recording). [onPress] and [onDisabledClick] as on [ShapedButton]
 * (Home's mic: hold to talk, and "not connected" when tapped while disabled).
 */
@Composable
fun OctagonActionButton(
    icon: ImageVector,
    contentDescription: String?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    outlined: Boolean = false,
    enabled: Boolean = true,
    size: Dp = 88.dp,
    interactionSource: MutableInteractionSource? = null,
    onPress: (suspend PressGestureScope.() -> Unit)? = null,
    onDisabledClick: (() -> Unit)? = null,
) {
    val colors = AlfredTheme.colors
    val filled = accentColors()
    ShapedButton(
        shape = OctagonShape,
        onClick = onClick,
        colors = { pressed, isEnabled ->
            if (!outlined) {
                filled(pressed, isEnabled)
            } else {
                when {
                    !isEnabled -> ButtonColors(colors.surface, colors.textDim, colors.outline)
                    pressed -> ButtonColors(colors.surfacePressed, colors.accent, colors.accent)
                    else -> ButtonColors(colors.surface, colors.accent, colors.accent)
                }
            }
        },
        modifier = modifier.size(size),
        enabled = enabled,
        borderWidth = 2.dp,
        interactionSource = interactionSource,
        onPress = onPress,
        onDisabledClick = onDisabledClick,
    ) { contentColor ->
        Icon(icon, contentDescription = contentDescription, tint = contentColor, modifier = Modifier.size(size * 0.4f))
    }
}

/** Full-width accent button with a letter-spaced label, e.g. "ENGAGE CLASSIC". */
@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    ShapedButton(
        shape = ChamferShape(12.dp),
        onClick = onClick,
        colors = accentColors(),
        modifier = modifier
            .fillMaxWidth()
            .height(56.dp),
        enabled = enabled,
    ) { contentColor ->
        Text(text.uppercase(), style = AlfredTheme.typography.button, color = contentColor)
    }
}

/** Accent pill pointed at both ends, e.g. "CUT IN" while Alfred is speaking. */
@Composable
fun HexagonButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    enabled: Boolean = true,
) {
    ShapedButton(
        shape = HexagonPillShape(14.dp),
        onClick = onClick,
        colors = accentColors(),
        modifier = modifier.height(60.dp),
        enabled = enabled,
    ) { contentColor ->
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.s),
            modifier = Modifier.padding(horizontal = 36.dp),
        ) {
            icon?.let { Icon(it, contentDescription = null, tint = contentColor, modifier = Modifier.size(20.dp)) }
            Text(text.uppercase(), style = AlfredTheme.typography.button, color = contentColor)
        }
    }
}
