package ru.igorek.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.Difference
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.Bridge

internal enum class SessionReviewTab { Plan, Changes, Versions, Agents, Files, Terminal, Browser }

@Composable
private fun reviewTabTitle(tab: SessionReviewTab): String = when (tab) {
    SessionReviewTab.Plan -> uiText("План", "Plan")
    SessionReviewTab.Changes -> uiText("Изменения", "Changes")
    SessionReviewTab.Versions -> uiText("Версии файлов", "File versions")
    SessionReviewTab.Agents -> uiText("Агенты", "Agents")
    SessionReviewTab.Files -> uiText("Файлы", "Files")
    SessionReviewTab.Terminal -> uiText("Терминал", "Terminal")
    SessionReviewTab.Browser -> uiText("Браузер", "Browser")
}

private fun reviewTabIcon(tab: SessionReviewTab): ImageVector = when (tab) {
    SessionReviewTab.Plan -> Icons.Outlined.Checklist
    SessionReviewTab.Changes -> Icons.Outlined.Difference
    SessionReviewTab.Versions -> Icons.Outlined.History
    SessionReviewTab.Agents -> Icons.Outlined.Groups
    SessionReviewTab.Files -> Icons.Outlined.FolderOpen
    SessionReviewTab.Terminal -> Icons.Outlined.Terminal
    SessionReviewTab.Browser -> Icons.Outlined.Language
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ReviewTabButton(tab: SessionReviewTab, active: Boolean, onClick: () -> Unit) {
    val label = reviewTabTitle(tab)
    TooltipBox(
        positionProvider = TooltipDefaults.rememberPlainTooltipPositionProvider(),
        tooltip = { PlainTooltip { Text(label) } },
        state = rememberTooltipState(),
    ) {
        IconButton(
            onClick = onClick,
            modifier = Modifier.size(48.dp)
                .background(if (active) MaterialTheme.colorScheme.primaryContainer else androidx.compose.ui.graphics.Color.Transparent, RoundedCornerShape(12.dp))
                .semantics { role = Role.Tab; selected = active },
        ) {
            Icon(reviewTabIcon(tab), contentDescription = label, modifier = Modifier.size(22.dp),
                tint = if (active) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ReviewNavigation(selectedTab: SessionReviewTab, onTabSelected: (SessionReviewTab) -> Unit) {
    var menuOpen by remember { mutableStateOf(false) }
    LaunchedEffect(selectedTab) { menuOpen = false }
    BoxWithConstraints(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp)) {
        val primary = listOf(SessionReviewTab.Plan, SessionReviewTab.Changes, SessionReviewTab.Versions, SessionReviewTab.Files)
        val visibleCount = ((maxWidth.value / 52f).toInt() - 1).coerceIn(1, primary.size)
        val visible = primary.take(visibleCount)
        val overflow = SessionReviewTab.entries.filter { it !in visible }
        val overflowActive = selectedTab in overflow
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            visible.forEach { tab -> ReviewTabButton(tab, tab == selectedTab) { onTabSelected(tab) } }
            Box {
                IconButton(
                    onClick = { menuOpen = true },
                    modifier = Modifier.size(48.dp).background(
                        if (overflowActive) MaterialTheme.colorScheme.primaryContainer else androidx.compose.ui.graphics.Color.Transparent,
                        RoundedCornerShape(12.dp),
                    ),
                ) {
                    Icon(Icons.Default.MoreHoriz, contentDescription = uiText("Другие разделы", "More sections"),
                        tint = if (overflowActive) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    overflow.forEach { tab ->
                        DropdownMenuItem(
                            text = { Text(reviewTabTitle(tab)) },
                            leadingIcon = { Icon(reviewTabIcon(tab), contentDescription = null, modifier = Modifier.size(20.dp)) },
                            trailingIcon = { if (tab == selectedTab) Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(18.dp)) },
                            onClick = { menuOpen = false; onTabSelected(tab) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
internal fun SessionReviewPanel(
    sessionId: String,
    selectedTab: SessionReviewTab,
    onTabSelected: (SessionReviewTab) -> Unit,
    onDismiss: () -> Unit,
    onOpenSession: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(modifier, color = MaterialTheme.colorScheme.surface, tonalElevation = 4.dp) {
        Column(Modifier.fillMaxSize()) {
            ReviewNavigation(selectedTab, onTabSelected)
            HorizontalDivider()
            when (selectedTab) {
                SessionReviewTab.Browser -> SessionBrowserPanel(onDismiss, Modifier.weight(1f))
                SessionReviewTab.Terminal -> SessionTerminalPanel(sessionId, onDismiss, Modifier.weight(1f))
                SessionReviewTab.Files -> SessionFilesPanel(sessionId, onDismiss, Modifier.weight(1f))
                SessionReviewTab.Agents -> AgentSessionsPanel(sessionId, onOpenSession, onDismiss, Modifier.weight(1f))
                SessionReviewTab.Plan -> SessionPlanPanel(onDismiss, Modifier.weight(1f))
                SessionReviewTab.Versions -> CheckpointsPanel(sessionId, onDismiss, Modifier.weight(1f))
                SessionReviewTab.Changes -> SessionChangesReviewPanel(sessionId, onDismiss, Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun SessionFilesPanel(sessionId: String, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    val files by Bridge.attachments.collectAsState()
    LaunchedEffect(sessionId) { Bridge.refreshFiles(sessionId) }
    Column(modifier) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(
                uiText("Файлы сессии", "Session files") + " (${files.size})",
                Modifier.weight(1f), style = MaterialTheme.typography.titleMedium,
            )
            IconButton(onClick = { Bridge.refreshFiles(sessionId) }) {
                Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить файлы", "Refresh files"))
            }
            IconButton(onClick = onDismiss) {
                Icon(Icons.Default.Close, contentDescription = uiText("Закрыть", "Close"))
            }
        }
        LazyColumn(
            Modifier.fillMaxSize(), contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (files.isEmpty()) item {
                Text(
                    uiText("Файлов пока нет. Файлы этой сессии появятся здесь по мере работы агента.", "No files yet. Files from this session will appear as the agent works."),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            items(files, key = { it.id }) { file -> AttachmentCard(sessionId, file) }
        }
    }
}

@Composable
private fun SessionPlanPanel(onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    val plan by Bridge.sessionPlan.collectAsState()
    Column(modifier.padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            val done = plan.steps.count { it.status == "completed" }
            Text(
                if (plan.steps.isEmpty()) uiText("План задачи", "Task plan")
                else uiText("Выполнено $done из ${plan.steps.size}", "$done of ${plan.steps.size} completed"),
                Modifier.weight(1f), style = MaterialTheme.typography.titleMedium,
            )
            IconButton(onClick = onDismiss) {
                Icon(Icons.Default.Close, contentDescription = uiText("Закрыть", "Close"))
            }
        }
        LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (plan.steps.isEmpty()) {
                item {
                    Text(uiText("План пока не опубликован. Шаги появятся, когда агент создаст план задачи.", "No plan published yet. Steps will appear when the agent creates a task plan."), style = MaterialTheme.typography.bodySmall)
                }
            }
            if (plan.explanation.isNotEmpty()) item {
                Text(plan.explanation, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            itemsIndexed(plan.steps) { index, step ->
                val color = when (step.status) {
                    "completed" -> Success
                    "in_progress" -> Warning
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                }
                val label = when (step.status) {
                    "completed" -> uiText("Готово", "Completed")
                    "in_progress" -> uiText("В работе", "In progress")
                    else -> uiText("Ожидает", "Pending")
                }
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text("${index + 1}. ${step.text}", style = MaterialTheme.typography.bodyMedium)
                    Text(label, style = MaterialTheme.typography.labelSmall, color = color)
                }
            }
        }
    }
}
