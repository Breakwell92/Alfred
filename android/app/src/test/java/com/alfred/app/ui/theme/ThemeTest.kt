package com.alfred.app.ui.theme

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ThemeTest {
    @Test
    fun `material colours come from Alfred's palette`() {
        val colors = AlfredDarkColors
        val scheme = colors.toColorScheme()
        assertEquals(colors.accent, scheme.primary)
        assertEquals(colors.onAccent, scheme.onPrimary)
        assertEquals(colors.background, scheme.background)
        assertEquals(colors.background, scheme.surface)
        assertEquals(colors.textPrimary, scheme.onSurface)
        assertEquals(colors.textSecondary, scheme.onSurfaceVariant)
        assertEquals(colors.surface, scheme.surfaceContainer)
        assertEquals(colors.record, scheme.error)
    }

    /** Acceptance criterion of STU-112: colour literals live only in `ui/theme/`. */
    @Test
    fun `no hard-coded colours outside the theme`() {
        val sources = File("src/main/java")
        assertTrue("run from the app module: ${sources.absolutePath}", sources.isDirectory)
        val offenders = sources.walk()
            .filter { it.isFile && it.extension == "kt" }
            .filterNot { it.invariantSeparatorsPath.contains("/ui/theme/") }
            .flatMap { file ->
                file.readLines().mapIndexedNotNull { i, line ->
                    if (COLOUR_LITERAL.containsMatchIn(line)) "${file.invariantSeparatorsPath}:${i + 1}: ${line.trim()}" else null
                }
            }
            .toList()
        assertEquals("Colour literals outside ui/theme:\n" + offenders.joinToString("\n"), emptyList<String>(), offenders)
    }

    @Test
    fun `colour literal pattern catches every way of writing a colour`() {
        listOf(
            "val a = Color(0xFF07080A)",
            "val b = Color(0.5f, 0.5f, 0.5f)",
            "val c = Color(255, 0, 0)",
            "val d = Color(red = 1f, green = 0f, blue = 0f)",
            "val e = Color( .5f, .5f, .5f)",
            "val f = Color.hsv(200f, 0.6f, 0.9f)",
            "val g = Color.hsl(200f, 0.6f, 0.5f)",
            "val h = Color.White",
            "val i = parse(\"#5AC8FA\")",
        ).forEach { assertTrue("should match: $it", COLOUR_LITERAL.containsMatchIn(it)) }
        listOf(
            "val a = Color.Transparent",
            "val b = Color.Unspecified",
            "val c = colors.accent",
            "val d = Color(colors.accent.value)",
            "val e = ButtonColors(container, content, null)",
            "import androidx.compose.ui.graphics.Color",
        ).forEach { assertFalse("should not match: $it", COLOUR_LITERAL.containsMatchIn(it)) }
    }

    private companion object {
        /**
         * A colour written out in code: `Color(` with a numeric or named-component argument,
         * `Color.hsv`/`Color.hsl`, a named constant like `Color.White`, or a `"#RRGGBB"` string.
         * `Color.Transparent` and `Color.Unspecified` aren't colours, so they're allowed.
         */
        val COLOUR_LITERAL = Regex(
            """\bColor\(\s*(-?\d|\.\d|(red|green|blue|alpha)\s*=)""" +
                """|\bColor\.(hsv|hsl)\(""" +
                """|\bColor\.(Black|White|Gray|DarkGray|LightGray|Red|Green|Blue|Yellow|Cyan|Magenta)\b""" +
                "|\"#[0-9A-Fa-f]{6,8}\"",
        )
    }
}
