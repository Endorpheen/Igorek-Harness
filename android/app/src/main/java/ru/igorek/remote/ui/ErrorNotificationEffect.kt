package ru.igorek.remote.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import ru.igorek.remote.data.Bridge

@Composable
internal fun ErrorNotificationEffect(message: String?, owner: String) {
    LaunchedEffect(owner, message) {
        if (!message.isNullOrBlank()) Bridge.reportUiError(message, owner)
    }
}
