package com.alfred.app.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Spacing scale. [gutter] is the screen's side margin in the design. */
@Immutable
data class AlfredSpacing(
    val xs: Dp = 4.dp,
    val s: Dp = 8.dp,
    val m: Dp = 12.dp,
    val l: Dp = 16.dp,
    val xl: Dp = 24.dp,
    val xxl: Dp = 32.dp,
    val gutter: Dp = 20.dp,
    /** Side of a cell in the background grid. */
    val gridCell: Dp = 24.dp,
)
