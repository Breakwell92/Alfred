package com.alfred.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Connectors list and controls arrive with STU-73. */
@Composable
fun ConnectorsScreen() = PlaceholderScreen("Connectors", "No connectors yet.")

/** Recent requests and actions; filled in by later tickets. */
@Composable
fun ActivityScreen() = PlaceholderScreen("Activity", "Nothing here yet.")

@Composable
private fun PlaceholderScreen(title: String, body: String) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        Text(body, style = MaterialTheme.typography.bodyMedium)
    }
}
