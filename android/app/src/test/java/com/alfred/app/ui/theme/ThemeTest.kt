package com.alfred.app.ui.theme

import java.io.File
import org.junit.Assert.assertEquals
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
        val literal = Regex("""Color\(\s*0x|Color\.(Black|White|Gray|DarkGray|LightGray|Red|Green|Blue|Yellow|Cyan|Magenta)\b|"#[0-9A-Fa-f]{6,8}"""")
        val offenders = sources.walk()
            .filter { it.isFile && it.extension == "kt" }
            .filterNot { it.invariantSeparatorsPath.contains("/ui/theme/") }
            .flatMap { file ->
                file.readLines().mapIndexedNotNull { i, line ->
                    if (literal.containsMatchIn(line)) "${file.invariantSeparatorsPath}:${i + 1}: ${line.trim()}" else null
                }
            }
            .toList()
        assertEquals("Colour literals outside ui/theme:\n" + offenders.joinToString("\n"), emptyList<String>(), offenders)
    }
}
