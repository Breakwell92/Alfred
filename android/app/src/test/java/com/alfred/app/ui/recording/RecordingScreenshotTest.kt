package com.alfred.app.ui.recording

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import com.alfred.app.data.voice.MicMeter
import com.alfred.app.ui.theme.AlfredTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The Recording screen mid-sentence, rendered on the JVM (Robolectric) and compared with the
 * golden image in `src/test/screenshots/` (Roborazzi). `./gradlew recordRoborazziDebug` updates
 * it after a deliberate change; CI runs `testDebugUnitTest` with `-Proborazzi.test.verify=true`.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
// A 1080 × 2340 phone at 440 dpi, as in the design.
@Config(sdk = [35], qualifiers = "w393dp-h851dp-port-440dpi")
class RecordingScreenshotTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun `populated recording screen`() {
        compose.setContent {
            AlfredTheme {
                RecordingContent(
                    elapsedMillis = 4_000,
                    stable = "Alfred, what's the fastest way to",
                    unstable = "the docks from here",
                    meter = { MicMeter(dbfs = -32f, level = 0.45f, noiseFilter = true) },
                    bars = { previewBars() },
                    onCancel = {},
                    onStop = {},
                )
            }
        }
        compose.onRoot().captureRoboImage("src/test/screenshots/recording_populated.png")
    }
}
