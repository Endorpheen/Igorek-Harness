package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.SessionOptionsResponse
import ru.igorek.remote.data.ProfileChoice
import ru.igorek.remote.R

@Composable
internal fun SessionProfilePicker(sessionId: String, profile: String, model: String, busy: Boolean,
    modifier: Modifier = Modifier) {
    val switching by Bridge.profileSwitching.collectAsState()
    val description = uiText("Выбрать профиль исполнителя", "Choose executor profile")
    var expanded by remember(sessionId) { mutableStateOf(false) }
    var profilesExpanded by remember(sessionId, profile) { mutableStateOf(false) }
    var loading by remember(sessionId) { mutableStateOf(false) }
    var options by remember(sessionId) { mutableStateOf<SessionOptionsResponse?>(null) }
    var failure by remember(sessionId) { mutableStateOf<String?>(null) }
    val request = remember(sessionId) { longArrayOf(0) }
    DisposableEffect(sessionId, profile) { onDispose { request[0]++ } }
    LaunchedEffect(profile) { expanded = false; loading = false; options = null; failure = null }
    fun load() {
        expanded = true
        profilesExpanded = false
        loading = true
        failure = null
        options = null
        val generation = ++request[0]
        Bridge.loadSessionOptions(profile) { result, error ->
            if (request[0] == generation) {
                loading = false
                options = result
                profilesExpanded = result?.switchableProfiles?.none { it.name == profile } == true
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
            modifier = Modifier.widthIn(min = 240.dp, max = 320.dp).heightIn(max = 480.dp)) {
            SessionProfileMenuContent(options, profile, model, busy, switching, loading, failure,
                profilesExpanded, onToggleProfiles = { profilesExpanded = !profilesExpanded },
                onProfile = { choice ->
                    if (choice == profile) expanded = false else {
                        request[0]++
                        Bridge.switchSessionProfile(sessionId, choice) { error ->
                            failure = error?.message
                            if (error == null) expanded = false
                        }
                    }
                }, onModel = { choice ->
                    if (choice == model) expanded = false else {
                        request[0]++
                        Bridge.switchSessionModel(sessionId, profile, choice) { error ->
                            failure = error?.message
                            if (error == null) expanded = false
                        }
                    }
                }, onRetry = { load() })
        }
    }
}

@Composable
internal fun SessionProfileMenuContent(options: SessionOptionsResponse?, profile: String, model: String,
    busy: Boolean, switching: Boolean, loading: Boolean, failure: String?, profilesExpanded: Boolean,
    onToggleProfiles: () -> Unit, onProfile: (String) -> Unit, onModel: (String) -> Unit,
    onRetry: () -> Unit) {
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
    val choices = options?.switchableProfiles.orEmpty()
    val currentChoice = choices.firstOrNull { it.name == profile }
    val otherChoices = choices.filter { it.name != profile }
    @Composable fun ProfileItem(choice: ProfileChoice, showIcon: Boolean) {
        val shownModel = if (choice.name == profile) model.ifBlank { choice.model } else choice.model
        DropdownMenuItem(text = {
            Column {
                Text(choice.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (shownModel.isNotBlank()) Text(modelDisplayName(shownModel),
                    style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }, leadingIcon = if (showIcon) {{ ProfileLogo(choice.displayProviderIcon) }} else null,
            trailingIcon = { if (choice.name == profile) Icon(Icons.Default.Check,
            contentDescription = uiText("Выбран", "Selected")) }, enabled = !busy && !switching,
            onClick = {
                onProfile(choice.name)
            })
    }
    currentChoice?.let { ProfileItem(it, false) }
    if (otherChoices.isNotEmpty()) {
        val showOthers = profilesExpanded
        val groupState = if (showOthers) uiText("Развёрнуто", "Expanded") else uiText("Свёрнуто", "Collapsed")
        DropdownMenuItem(text = { Text(uiText("Другие профили", "Other profiles") + " · " + otherChoices.size) },
            modifier = Modifier.semantics { stateDescription = groupState },
            trailingIcon = { Icon(Icons.Default.ArrowDropDown, contentDescription = null,
                modifier = Modifier.rotate(if (showOthers) 180f else 0f)) },
            onClick = onToggleProfiles)
        if (showOthers) Column(Modifier.heightIn(max = 180.dp).verticalScroll(rememberScrollState())) {
            otherChoices.forEach { ProfileItem(it, true) }
        }
    }
    if (currentChoice?.modelsStatus == "unavailable") Text(uiText("Не удалось загрузить модели провайдера. Повторите позже.",
        "Could not load provider models. Try again later."), Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium)
    currentChoice?.models.orEmpty().let { models ->
        if (models.isNotEmpty()) Text(uiText("Модели провайдера", "Provider models"),
            Modifier.padding(start = 16.dp, top = 12.dp), style = MaterialTheme.typography.labelMedium)
        models.forEach { choice ->
            DropdownMenuItem(text = { Text(currentChoice?.modelLabels?.get(choice) ?: modelDisplayName(choice)) },
                trailingIcon = { if (choice == model) Icon(Icons.Default.Check,
                    contentDescription = uiText("Выбран", "Selected")) }, enabled = !busy && !switching,
                onClick = {
                    onModel(choice)
                })
        }
    }
    if (failure != null) DropdownMenuItem(text = { Text(uiText("Повторить", "Retry")) }, onClick = onRetry)
}

@Composable
private fun ProfileLogo(provider: String) {
    val resource = when (provider) {
        "openai" -> R.drawable.provider_openai
        "anthropic" -> R.drawable.provider_anthropic
        "opencode" -> R.drawable.provider_opencode_mark
        "openrouter" -> R.drawable.provider_openrouter
        "zai" -> R.drawable.provider_zai
        "minimax" -> R.drawable.provider_minimax
        "deepseek" -> R.drawable.provider_deepseek
        "moonshotai" -> R.drawable.provider_moonshotai
        "google" -> R.drawable.provider_google
        "xai" -> R.drawable.provider_xai
        "inception" -> R.drawable.provider_inception_mark
        "local" -> R.drawable.provider_local
        else -> R.drawable.provider_custom
    }
    Icon(painterResource(resource), contentDescription = null, modifier = Modifier.size(20.dp))
}
