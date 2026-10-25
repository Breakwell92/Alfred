package com.alfred.app.ui.home

import android.app.Application
import androidx.compose.runtime.Composable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.settings.ProfileSettings
import com.alfred.app.data.voice.LinkState
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.theme.AlfredTheme
import com.github.takahirom.roborazzi.captureRoboImage
import java.time.LocalTime
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Home idle, rendered on the JVM (Robolectric) and compared with the goldens in
 * `src/test/screenshots/` (Roborazzi), as in `RecordingScreenshotTest`. The orb never stops
 * animating, so the test drives Compose's clock by hand and shoots a fixed moment.
 * A plain [Application] stands in for `AlfredApplication`, which starts Koin once per JVM.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
// A 1080 × 2340 phone at 440 dpi, as in the design.
@Config(sdk = [35], qualifiers = "w393dp-h851dp-port-440dpi", application = Application::class)
class HomeScreenshotTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun `idle and connected`() {
        compose.setContent { Home(ConnectionStatus.Connected, LinkState.Connected("s1", voice = true)) }
        shoot("home_idle_connected.png")
    }

    @Test
    fun `offline`() {
        compose.setContent {
            Home(ConnectionStatus.Unreachable("timeout"), LinkState.Waiting("connection refused", 4_000))
        }
        shoot("home_offline.png")
    }

    @Test
    @Config(qualifiers = "w360dp-h640dp-port-xhdpi")
    fun `idle on a small screen`() {
        compose.setContent { Home(ConnectionStatus.Connected, LinkState.Connected("s1", voice = true)) }
        shoot("home_idle_small.png")
    }

    @Before
    fun stopTheClock() {
        compose.mainClock.autoAdvance = false
    }

    private fun shoot(file: String) {
        compose.mainClock.advanceTimeBy(FRAME_TIME_MILLIS)
        compose.onRoot().captureRoboImage("src/test/screenshots/$file")
    }

    @Test
    @Config(qualifiers = "w360dp-h640dp-port-xhdpi")
    fun `a long place keeps the connection state readable`() {
        compose.setContent {
            Home(
                ConnectionStatus.Connected,
                LinkState.Connected("s1", voice = true),
                ProfileSettings(location = "Kingston upon Thames"),
            )
        }
        shoot("home_long_place_small.png")
    }

    @Composable
    private fun Home(status: ConnectionStatus, link: LinkState, profile: ProfileSettings = ProfileSettings()) {
        AlfredTheme {
            GridBackground {
                HomeContent(
                    status = status,
                    voice = VoiceUiState(link = link),
                    level = { 0f },
                    profile = profile,
                    time = LocalTime.of(2, 14),
                    micDenied = false,
                    onOpenMenu = {},
                    onOpenProtocol = {},
                    onCheckNow = {},
                    onTalkPressed = { true },
                    onTalkReleased = {},
                    onMicUnavailable = {},
                    onOpenAppSettings = {},
                    onAnswer = {},
                    onDismissConfirmation = {},
                )
            }
        }
    }

    private companion object {
        /** Long enough for the sweep to have turned a little and the core to have settled. */
        const val FRAME_TIME_MILLIS = 1_200L
    }
}
