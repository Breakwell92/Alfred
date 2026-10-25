package com.alfred.app.ui

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavController
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.home.HomeScreen
import com.alfred.app.ui.recording.RecordingScreen
import com.alfred.app.ui.recording.RecordingViewModel
import com.alfred.app.ui.settings.SettingsScreen
import com.alfred.app.ui.theme.AlfredTheme
import com.alfred.app.ui.transmitting.TransmittingScreen
import com.alfred.app.ui.transmitting.TransmittingViewModel
import kotlinx.coroutines.launch
import org.koin.compose.viewmodel.koinViewModel

enum class Destination(val route: String, val label: String, val icon: ImageVector) {
    Home("home", "Home", Icons.Filled.Home),
    VoiceProtocol("voice_protocol", "Voice Protocol", Icons.Filled.GraphicEq),
    Connectors("connectors", "Connectors", Icons.Filled.Extension),
    Activity("activity", "Activity", Icons.Filled.History),
    Settings("settings", "Settings", Icons.Filled.Settings),
}

/**
 * Home is the start and the hub (ADR 0022): its menu button opens the drawer, and every other
 * screen goes back to it. There's no bottom bar; the design has none.
 */
@Composable
fun AlfredApp(
    recordingViewModel: RecordingViewModel = koinViewModel(),
    transmittingViewModel: TransmittingViewModel = koinViewModel(),
) {
    val recording by recordingViewModel.state.collectAsStateWithLifecycle()
    val transmitting by transmittingViewModel.state.collectAsStateWithLifecycle()
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = backStackEntry?.destination
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()

    // The grid shows through every screen: the scaffold and screens draw no background of their own.
    GridBackground(Modifier.fillMaxSize()) {
        ModalNavigationDrawer(
            drawerState = drawer,
            // Opened from Home's menu button; swiping only closes it.
            gesturesEnabled = drawer.isOpen,
            drawerContent = {
                ModalDrawerSheet(drawerContainerColor = AlfredTheme.colors.surface) {
                    Spacer(Modifier.height(AlfredTheme.spacing.l))
                    Destination.entries.forEach { destination ->
                        NavigationDrawerItem(
                            label = { Text(destination.label) },
                            icon = { Icon(destination.icon, contentDescription = null) },
                            selected = currentDestination?.hierarchy?.any { it.route == destination.route } == true,
                            onClick = {
                                scope.launch { drawer.close() }
                                navController.goTo(destination)
                            },
                            modifier = Modifier.padding(horizontal = AlfredTheme.spacing.m),
                        )
                    }
                }
            },
        ) {
            Scaffold(
                containerColor = Color.Transparent,
                // A transparent container gives no content colour, so text would default to black.
                contentColor = MaterialTheme.colorScheme.onBackground,
            ) { innerPadding ->
                NavHost(
                    navController = navController,
                    startDestination = Destination.Home.route,
                    modifier = Modifier.padding(innerPadding),
                ) {
                    composable(Destination.Home.route) {
                        HomeScreen(
                            onOpenMenu = { scope.launch { drawer.open() } },
                            onOpenProtocol = { navController.goTo(Destination.VoiceProtocol) },
                        )
                    }
                    composable(Destination.VoiceProtocol.route) { VoiceProtocolScreen() }
                    composable(Destination.Connectors.route) { ConnectorsScreen() }
                    composable(Destination.Activity.route) { ActivityScreen() }
                    composable(Destination.Settings.route) { SettingsScreen() }
                }
            }
        }
        // Recording is a layer over everything, not a destination (ADR 0021): Home stays
        // underneath, so a hold-to-talk press that opened it still sends when let go.
        if (recording.listening) {
            RecordingScreen(recordingViewModel, Modifier.fillMaxSize())
        } else if (transmitting.visible) {
            // Alfred's reply, the same way (ADR 0023).
            TransmittingScreen(transmittingViewModel, Modifier.fillMaxSize())
        }
    }
}

/** At most one screen above Home, so back always returns there. */
private fun NavController.goTo(destination: Destination) {
    navigate(destination.route) {
        popUpTo(graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
