package com.alfred.app.ui.theme

import androidx.compose.foundation.shape.CornerSize
import androidx.compose.foundation.shape.CutCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.runtime.Immutable
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import kotlin.math.sqrt

/** A corner of a shape, relative to the layout direction (start is the right in RTL). */
enum class Corner { TopStart, TopEnd, BottomEnd, BottomStart }

/** The cut the design uses on icon buttons, tiles and list cards. */
val DiagonalCorners: Set<Corner> = setOf(Corner.TopStart, Corner.BottomEnd)

/**
 * A rectangle with 45° cuts of [cut] on [corners] (capped at half the shorter side). It's a
 * [CutCornerShape], so it works anywhere Material takes a shape; with `Modifier.clip` it clips
 * ripples, and with `Modifier.border` borders follow the cut.
 */
fun ChamferShape(cut: Dp, corners: Set<Corner> = DiagonalCorners): CutCornerShape {
    fun size(corner: Corner) = if (corner in corners) cut else 0.dp
    return CutCornerShape(
        topStart = size(Corner.TopStart),
        topEnd = size(Corner.TopEnd),
        bottomEnd = size(Corner.BottomEnd),
        bottomStart = size(Corner.BottomStart),
    )
}

/**
 * A flat-topped octagon, regular when the button is square: the main action button. A regular
 * octagon's corner cut is side × (1 − 1/√2).
 */
val OctagonShape: CutCornerShape = CutCornerShape(OctagonCut)

/**
 * The octagon's cut as a fraction of the shorter side. `CornerSize(Float)` would be pixels and
 * `CornerSize(Int)` whole percent, so this computes it exactly.
 */
private object OctagonCut : CornerSize {
    override fun toPx(shapeSize: Size, density: Density): Float =
        shapeSize.minDimension * (1f - 1f / sqrt(2f))
}

/**
 * An elongated hexagon pointed at both ends, like the CUT IN pill. [point] is how far each tip
 * reaches past the flat top and bottom edges (steeper than a 45° cut, as in the design).
 */
@Immutable
class HexagonPillShape(private val point: Dp = 12.dp) : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline =
        Outline.Generic(
            Path().apply {
                hexagonPillPoints(size, with(density) { point.toPx() }).forEachIndexed { i, p ->
                    if (i == 0) moveTo(p.x, p.y) else lineTo(p.x, p.y)
                }
                close()
            },
        )

    override fun equals(other: Any?): Boolean = other is HexagonPillShape && other.point == point

    override fun hashCode(): Int = point.hashCode()

    override fun toString(): String = "HexagonPillShape(point=$point)"
}

/** Clockwise from the top-left tip. Pure geometry, so it's unit-tested. */
internal fun hexagonPillPoints(size: Size, pointPx: Float): List<Offset> {
    val w = size.width
    val h = size.height
    val p = pointPx.coerceIn(0f, w / 2)
    return listOf(
        Offset(p, 0f),
        Offset(w - p, 0f),
        Offset(w, h / 2),
        Offset(w - p, h),
        Offset(p, h),
        Offset(0f, h / 2),
    )
}

/** Material's shape scale as chamfers, so stock cards, fields and sheets match. */
val AlfredMaterialShapes = Shapes(
    extraSmall = ChamferShape(4.dp),
    small = ChamferShape(6.dp),
    medium = ChamferShape(10.dp),
    large = ChamferShape(12.dp),
    extraLarge = ChamferShape(16.dp, setOf(Corner.TopStart, Corner.TopEnd)),
)
