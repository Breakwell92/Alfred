package com.alfred.app.ui.theme

import androidx.compose.foundation.shape.CutCornerShape
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class ShapesTest {
    private val density = Density(2f)
    private val size = Size(200f, 80f)

    /** Cut sizes in px as (topStart, topEnd, bottomEnd, bottomStart). */
    private fun CutCornerShape.cuts(size: Size = this@ShapesTest.size) = listOf(
        topStart.toPx(size, density),
        topEnd.toPx(size, density),
        bottomEnd.toPx(size, density),
        bottomStart.toPx(size, density),
    )

    @Test
    fun `chamfer cuts only the given corners`() {
        assertEquals(listOf(16f, 0f, 16f, 0f), ChamferShape(8.dp).cuts())
        assertEquals(listOf(0f, 0f, 16f, 0f), ChamferShape(8.dp, setOf(Corner.BottomEnd)).cuts())
        assertEquals(listOf(0f, 0f, 0f, 0f), ChamferShape(8.dp, emptySet()).cuts())
        assertEquals(
            listOf(16f, 16f, 16f, 16f),
            ChamferShape(8.dp, Corner.entries.toSet()).cuts(),
        )
    }

    @Test
    fun `octagon is regular on a square of any size`() {
        // 100 px once hid a px-for-percent mix-up, so check sizes where they differ.
        listOf(48f, 176f, 385f).forEach { side ->
            val cut = OctagonShape.cuts(Size(side, side)).distinct().single()
            val straight = side - 2 * cut
            val diagonal = Offset(cut, cut).getDistance()
            assertEquals("side $side", straight, diagonal, 0.01f)
        }
    }

    @Test
    fun `hexagon pill points at mid height`() {
        assertEquals(
            listOf(
                Offset(12f, 0f), Offset(188f, 0f), Offset(200f, 40f),
                Offset(188f, 80f), Offset(12f, 80f), Offset(0f, 40f),
            ),
            hexagonPillPoints(size, 12f),
        )
    }

    @Test
    fun `hexagon tips never cross`() {
        val points = hexagonPillPoints(Size(10f, 80f), 50f)
        assertEquals(5f, points[0].x)
        assertEquals(5f, points[1].x)
    }

    @Test
    fun `shapes compare by value so recomposition can skip`() {
        assertEquals(ChamferShape(8.dp), ChamferShape(8.dp, DiagonalCorners))
        assertNotEquals(ChamferShape(8.dp), ChamferShape(8.dp, setOf(Corner.BottomEnd)))
        assertEquals(HexagonPillShape(12.dp), HexagonPillShape(12.dp))
    }
}
