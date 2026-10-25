package com.alfred.app.ui.transmitting

import android.app.Application
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.ui.theme.AlfredTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The Transmitting screen mid-reply and while thinking, compared with the goldens in
 * `src/test/screenshots/` (Roborazzi). The orb never stops animating, so Compose's clock is
 * driven by hand and each capture is of the same frame. `./gradlew recordRoborazziDebug`
 * updates the goldens after a deliberate change.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
// A 1080 × 2340 phone at 440 dpi, as in the design. A plain Application: AlfredApplication starts
// Koin, which can only happen once per JVM.
@Config(sdk = [35], qualifiers = "w393dp-h851dp-port-440dpi", application = Application::class)
class TransmittingScreenshotTest {

    @get:Rule
    val compose = createComposeRule()

    private fun capture(state: TransmittingUiState, heard: Long, name: String) {
        compose.mainClock.autoAdvance = false
        compose.setContent {
            AlfredTheme {
                TransmittingContent(
                    state = state,
                    heard = { heard },
                    level = { 0.5f },
                    onClose = {},
                    onCutIn = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1_000)
        compose.onRoot().captureRoboImage("src/test/screenshots/$name.png")
    }

    @Test
    fun `speaking, part-way through the reply`() {
        val state = previewState()
        capture(state, previewHeard(state), "transmitting_speaking")
    }

    @Test
    fun `thinking, reply arriving`() {
        val state = previewState().copy(
            phase = VoicePhase.Thinking,
            spans = emptyList(),
            highlight = Highlight.Pending,
        )
        capture(state, 0, "transmitting_thinking")
    }
}
