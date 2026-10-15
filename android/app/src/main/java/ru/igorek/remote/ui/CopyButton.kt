package ru.igorek.remote.ui

import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

internal const val COPY_FEEDBACK_MS = 1_200L

/**
 * Compact copy button: copies [text] into the system clipboard and replaces the icon with a checkmark after [COPY_FEEDBACK_MS] milliseconds.
 */
@Composable
internal fun CopyButton(text: String, modifier: Modifier = Modifier) {
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) {
        if (copied) {
            delay(COPY_FEEDBACK_MS)
            copied = false
        }
    }
    IconButton(
        onClick = {
            clipboard.setText(AnnotatedString(text))
            copied = true
        },
        modifier = modifier.size(32.dp),
    ) {
        Icon(
            imageVector = if (copied) Icons.Default.Check else Icons.Default.ContentCopy,
            contentDescription = if (copied) uiText("Скопировано", "Copied") else uiText("Копировать", "Copy"),
            modifier = Modifier.size(16.dp),
            tint = if (copied) Success else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
