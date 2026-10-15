package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Webhook
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.McpServerDto
import ru.igorek.remote.data.SkillDto

private enum class ExtensionTab { Skills, Mcp, Hooks }

@Composable
internal fun ExtensionsScreen(
    onMenu: () -> Unit,
    canUseSkill: Boolean,
    onUseSkill: (String) -> Unit,
) {
    val payload by Bridge.extensions.collectAsState()
    var profiles by remember { mutableStateOf<List<String>>(emptyList()) }
    var profile by remember { mutableStateOf("") }
    var profileMenu by remember { mutableStateOf(false) }
    var selectedTab by remember { mutableStateOf(ExtensionTab.Skills) }
    var query by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }
    var busySkill by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        Bridge.loadSessionOptions { options, _ ->
            profiles = options?.profiles.orEmpty()
            profile = options?.defaultProfile.orEmpty().ifBlank { profiles.firstOrNull().orEmpty() }
            if (profile.isBlank()) loading = false
        }
    }
    LaunchedEffect(profile) {
        if (profile.isNotBlank()) {
            loading = true
            Bridge.refreshExtensions(profile) { loading = false }
        }
    }

    Scaffold(topBar = {
        ControlTopBar(
            uiText("Расширения", "Extensions"),
            uiText("Скиллы, MCP и хуки агента", "Agent skills, MCP, and hooks"),
            onMenu = onMenu,
            actions = {
                IconButton(
                    onClick = {
                        if (profile.isNotBlank()) {
                            loading = true
                            Bridge.refreshExtensions(profile) { loading = false }
                        }
                    },
                ) {
                    Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить", "Refresh"))
                }
            },
        )
    }) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).padding(horizontal = 14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { profileMenu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(
                        if (profile.isBlank()) uiText("Профиль не выбран", "No profile selected")
                        else uiText("Профиль: $profile", "Profile: $profile"),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                DropdownMenu(
                    expanded = profileMenu,
                    onDismissRequest = { profileMenu = false },
                ) {
                    profiles.forEach { item ->
                        DropdownMenuItem(
                            text = { Text(item, fontFamily = FontFamily.Monospace) },
                            onClick = {
                                profile = item
                                profileMenu = false
                            },
                        )
                    }
                }
            }

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterChip(
                    selected = selectedTab == ExtensionTab.Skills,
                    onClick = { selectedTab = ExtensionTab.Skills },
                    label = { Text(uiText("Скиллы ${payload.skills.size}", "Skills ${payload.skills.size}")) },
                )
                FilterChip(
                    selected = selectedTab == ExtensionTab.Mcp,
                    onClick = { selectedTab = ExtensionTab.Mcp },
                    label = { Text("MCP ${payload.mcpServers.size}") },
                )
                FilterChip(
                    selected = selectedTab == ExtensionTab.Hooks,
                    onClick = { selectedTab = ExtensionTab.Hooks },
                    label = { Text(uiText("Хуки ${payload.hooks.size}", "Hooks ${payload.hooks.size}")) },
                )
            }

            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                placeholder = { Text(uiText("Поиск расширений", "Search extensions")) },
            )

            if (loading && payload.profile != profile) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else {
                when (selectedTab) {
                    ExtensionTab.Skills -> SkillsList(
                        skills = payload.skills.filter {
                            query.isBlank() || it.name.contains(query, true) ||
                                it.description.contains(query, true) || it.category.contains(query, true)
                        },
                        busySkill = busySkill,
                        canUseSkill = canUseSkill,
                        onToggle = { skill, enabled ->
                            busySkill = skill.skillId
                            Bridge.setSkillEnabled(skill.skillId, enabled) { busySkill = null }
                        },
                        onUseSkill = onUseSkill,
                    )
                    ExtensionTab.Mcp -> McpList(
                        servers = payload.mcpServers.filter {
                            query.isBlank() || it.name.contains(query, true) ||
                                it.description.contains(query, true) ||
                                it.capabilities.any { capability -> capability.contains(query, true) }
                        },
                    )
                    ExtensionTab.Hooks -> HooksState(payload.hooksAvailable)
                }
            }
        }
    }
}

@Composable
private fun SkillsList(
    skills: List<SkillDto>,
    busySkill: String?,
    canUseSkill: Boolean,
    onToggle: (SkillDto, Boolean) -> Unit,
    onUseSkill: (String) -> Unit,
) {
    if (skills.isEmpty()) {
        ExtensionEmptyState(
            Icons.Default.Extension,
            uiText("Скиллы не найдены", "No skills found"),
            uiText("Измените поиск или проверьте настройки runtime.", "Change the search or check runtime settings."),
        )
        return
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(skills, key = { it.skillId }) { skill ->
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(skill.name, fontWeight = FontWeight.SemiBold)
                            if (skill.category.isNotBlank()) {
                                Text(
                                    skill.category,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary,
                                )
                            }
                        }
                        if (busySkill == skill.skillId) {
                            CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp)
                        } else {
                            Switch(
                                checked = skill.enabled,
                                onCheckedChange = { onToggle(skill, it) },
                            )
                        }
                    }
                    Text(
                        skill.description.ifBlank { uiText("Описание не указано", "No description") },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        AssistChip(
                            onClick = {},
                            label = {
                                Text(
                                    when {
                                        !skill.supported -> uiText("Не для профиля", "Unsupported")
                                        skill.enabled -> uiText("Включён", "Enabled")
                                        else -> uiText("Выключен", "Disabled")
                                    },
                                )
                            },
                        )
                        Button(
                            onClick = { onUseSkill(skill.skillId) },
                            enabled = canUseSkill && skill.enabled && skill.supported,
                        ) {
                            Text(uiText("В чат", "Use in chat"))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun McpList(servers: List<McpServerDto>) {
    if (servers.isEmpty()) {
        ExtensionEmptyState(
            Icons.Default.Build,
            uiText("MCP-серверы не настроены", "No MCP servers configured"),
            uiText("Для выбранного профиля нет зарегистрированных MCP-возможностей.", "No MCP capabilities are registered for this profile."),
        )
        return
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(servers, key = { it.name }) { server ->
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Build, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                        Column(Modifier.weight(1f).padding(start = 10.dp)) {
                            Text(server.name, fontWeight = FontWeight.SemiBold)
                            Text(
                                if (server.enabled) uiText("Подключён к профилю", "Enabled for profile")
                                else uiText("Не подключён к профилю", "Not enabled for profile"),
                                style = MaterialTheme.typography.labelSmall,
                                color = if (server.enabled) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    if (server.description.isNotBlank()) {
                        Text(server.description, style = MaterialTheme.typography.bodySmall)
                    }
                    val tools = server.toolsHint.ifEmpty { server.capabilities }
                    if (tools.isNotEmpty()) {
                        Text(
                            uiText("Возможности: ", "Capabilities: ") + tools.joinToString(", "),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (server.requiresEnv.isNotEmpty()) {
                        Text(
                            uiText("Нужны переменные окружения: ", "Required environment: ") + server.requiresEnv.joinToString(", "),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.tertiary,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun HooksState(available: Boolean) {
    ExtensionEmptyState(
        Icons.Default.Webhook,
        if (available) uiText("Хуки пока не настроены", "No hooks configured")
        else uiText("Управление хуками ещё не подключено", "Hook management is not connected yet"),
        if (available) uiText("Настроенные lifecycle-хуки появятся здесь.", "Configured lifecycle hooks will appear here.")
        else uiText(
            "В runtime есть заготовка интерфейса, но рабочего API хуков пока нет.",
            "The runtime has UI scaffolding, but no functional hooks API yet.",
        ),
    )
}

@Composable
private fun ExtensionEmptyState(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, text: String) {
    Column(
        Modifier.fillMaxWidth().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(42.dp), tint = MaterialTheme.colorScheme.primary)
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
