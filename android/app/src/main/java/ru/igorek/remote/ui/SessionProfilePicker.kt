package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.SessionOptionsResponse

@Composable
internal fun SessionProfilePicker(sessionId: String, profile: String, model: String, busy: Boolean,
    modifier: Modifier = Modifier) {
    val switching by Bridge.profileSwitching.collectAsState()
    val description = uiText("Выбрать профиль исполнителя", "Choose executor profile")
    var expanded by remember(sessionId) { mutableStateOf(false) }
    var loading by remember(sessionId) { mutableStateOf(false) }
    var options by remember(sessionId) { mutableStateOf<SessionOptionsResponse?>(null) }
    var failure by remember(sessionId) { mutableStateOf<String?>(null) }
    val request = remember(sessionId) { longArrayOf(0) }
    DisposableEffect(sessionId, profile) { onDispose { request[0]++ } }
    LaunchedEffect(profile) { expanded = false; loading = false; options = null; failure = null }
    fun load() {
        expanded = true
        loading = true
        failure = null
        options = null
        val generation = ++request[0]
        Bridge.loadSessionOptions(profile) { result, error ->
            if (request[0] == generation) {
                loading = false
                options = result
                failure = error?.message
            }
        }
    }
    Box(modifier) {
        TextButton(onClick = { load() }, enabled = !switching,
            modifier = Modifier.fillMaxWidth().semantics {
                contentDescription = description
            }) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(if (switching) uiText("Переключение…", "Switching…") else
                        profile.ifBlank { uiText("Профиль", "Profile") }, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (model.isNotBlank()) Text(modelDisplayName(model), style = MaterialTheme.typography.labelSmall,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Icon(Icons.Default.ArrowDropDown, contentDescription = null)
            }
        }
        ErrorNotificationEffect(failure, "profile-picker")
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false; request[0]++ },
            modifier = Modifier.widthIn(min = 240.dp, max = 320.dp)) {
            val hint = when {
                loading -> uiText("Загрузка профилей…", "Loading profiles…")
                failure != null -> null
                options?.profileSwitchSupported != true -> uiText(
                    "Переключение профиля сейчас недоступно", "Profile switching is currently unavailable")
                busy -> uiText("Дождитесь завершения текущей задачи или отправки", "Wait for the current task or send to finish")
                options?.switchableProfiles.isNullOrEmpty() -> uiText("Нет доступных профилей", "No available profiles")
                else -> null
            }
            if (hint != null) Text(hint, Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium)
            options?.switchableProfiles.orEmpty().forEach { choice ->
                DropdownMenuItem(text = {
                    Column {
                        Text(choice.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (choice.model.isNotBlank()) Text(modelDisplayName(choice.model),
                            style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }, trailingIcon = { if (choice.name == profile) Icon(Icons.Default.Check,
                    contentDescription = uiText("Выбран", "Selected")) }, enabled = !busy && !switching,
                    onClick = {
                        if (choice.name == profile) { expanded = false; return@DropdownMenuItem }
                        request[0]++
                        Bridge.switchSessionProfile(sessionId, choice.name) { error ->
                            failure = error?.message
                            if (error == null) expanded = false
                        }
                    })
            }
            val currentChoice = options?.switchableProfiles?.firstOrNull { it.name == profile }
            if (currentChoice?.modelsStatus == "unavailable") Text(uiText("Не удалось загрузить модели аккаунта. Повторите позже.",
                "Could not load account models. Try again later."), Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium)
            currentChoice?.models.orEmpty().let { models ->
                if (models.isNotEmpty()) Text(uiText("Модель для этого чата", "Model for this chat"),
                    Modifier.padding(start = 16.dp, top = 12.dp), style = MaterialTheme.typography.labelMedium)
                models.forEach { choice ->
                    DropdownMenuItem(text = { Text(currentChoice?.modelLabels?.get(choice) ?: modelDisplayName(choice)) },
                        trailingIcon = { if (choice == model) Icon(Icons.Default.Check,
                            contentDescription = uiText("Выбран", "Selected")) }, enabled = !busy && !switching,
                        onClick = {
                            if (choice == model) { expanded = false; return@DropdownMenuItem }
                            request[0]++
                            Bridge.switchSessionModel(sessionId, profile, choice) { error ->
                                failure = error?.message
                                if (error == null) expanded = false
                            }
                        })
                }
            }
            if (failure != null) DropdownMenuItem(text = { Text(uiText("Повторить", "Retry")) }, onClick = { load() })
        }
    }
}
