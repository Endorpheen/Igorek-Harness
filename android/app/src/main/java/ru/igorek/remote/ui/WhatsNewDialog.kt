package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.NewReleases
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import ru.igorek.remote.BuildConfig

@Composable
internal fun WhatsNewDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    val changes = remember(context) {
        context.assets.open("release-notes.json").bufferedReader().use {
            JSONObject(it.readText()).getJSONArray("changes")
        }
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(24.dp),
        icon = { Icon(Icons.Default.NewReleases, contentDescription = null, tint = MaterialTheme.colorScheme.primary) },
        title = { Text(uiText("Что нового", "What's new")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text(
                    "Igorek Remote ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
                HorizontalDivider(color = MaterialTheme.colorScheme.outline)
                Column(
                    Modifier.heightIn(max = 320.dp).verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(20.dp),
                ) {
                    repeat(changes.length()) { index ->
                        val change = changes.getJSONObject(index)
                        val russian = change.getJSONObject("ru")
                        val english = change.getJSONObject("en")
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(uiText(russian.getString("title"), english.getString("title")), style = MaterialTheme.typography.titleSmall)
                            Text(
                                uiText(russian.getString("text"), english.getString("text")),
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(uiText("Понятно", "Got it")) } },
    )
}
