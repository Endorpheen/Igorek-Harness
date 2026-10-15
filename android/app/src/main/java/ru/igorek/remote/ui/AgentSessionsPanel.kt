package ru.igorek.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AgentSessionDto
import ru.igorek.remote.data.Bridge

internal fun orderedAgentSessions(sessions: List<AgentSessionDto>): List<Pair<AgentSessionDto, Int>> {
    val ids = sessions.map { it.sessionId }.toSet()
    val children = sessions.groupBy { it.parentSessionId?.takeIf { parent -> parent in ids } }
    val seen = mutableSetOf<String>()
    val result = mutableListOf<Pair<AgentSessionDto, Int>>()
    fun visit(node: AgentSessionDto, depth: Int) {
        if (!seen.add(node.sessionId)) return
        result.add(node to depth)
        children[node.sessionId].orEmpty().forEach { visit(it, depth + 1) }
    }
    children[null].orEmpty().forEach { visit(it, 0) }
    sessions.forEach { visit(it, 0) }
    return result
}

@Composable
internal fun AgentSessionsPanel(sessionId: String, onOpen: (String) -> Unit, onDismiss: () -> Unit, modifier: Modifier) {
    val tree by Bridge.agentSessions.collectAsState()
    val error by Bridge.agentSessionsError.collectAsState()
    Column(modifier.padding(horizontal = 12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(uiText("Агентные сессии", "Agent sessions"), Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
            IconButton(onClick = { Bridge.refreshAgentSessions(sessionId) }) { Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить", "Refresh")) }
            IconButton(onClick = onDismiss) { Icon(Icons.Default.Close, contentDescription = uiText("Закрыть", "Close")) }
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (tree.sessions.isEmpty() && error == null) item { Text(uiText("Загружаем агентов…", "Loading agents…")) }
            items(orderedAgentSessions(tree.sessions), key = { it.first.sessionId }) { (node, depth) ->
                Column(
                    Modifier.fillMaxWidth().clickable { if (node.sessionId != sessionId) onOpen(node.sessionId) }
                        .padding(start = (8 + minOf(depth, 8) * 12).dp, top = 12.dp, bottom = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        if (node.parentSessionId == null) "Main" else node.title.ifBlank { node.role },
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (node.sessionId == sessionId) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                    )
                    val label = when (node.status) {
                        "running" -> uiText("Работает", "Running")
                        "waiting_approval" -> uiText("Ждёт подтверждения", "Waiting for approval")
                        "waiting_input" -> uiText("Ждёт ответа", "Waiting for answer")
                        "completed" -> uiText("Завершён", "Completed")
                        "failed" -> uiText("Ошибка", "Failed")
                        "cancelled" -> uiText("Остановлен", "Cancelled")
                        else -> node.status
                    }
                    Text(label, style = MaterialTheme.typography.labelSmall)
                    if (node.activity.isNotBlank()) Text(node.activity, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    HorizontalDivider()
                }
            }
        }
    }
}
