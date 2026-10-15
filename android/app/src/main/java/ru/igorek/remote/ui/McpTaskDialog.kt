package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.SecureFlagPolicy
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeException

internal fun mcpFailure(code: String): String = when (code) {
    "vault_locked", "interface_locked" -> "Разблокируйте хранилище на сервере."
    "mcp_access_denied" -> "Доступ запрещён или отозван. Проверьте права MCP."
    "task_other_window" -> "MCP используется другим устройством."
    "task_replaced" -> "Подключение заменено. Выберите его заново."
    else -> "MCP недоступен. Проверьте подключение и выбранную модель."
}

internal fun mcpFailure(error: Throwable): String = when {
    error is BridgeException && error.status == 404 -> "На сервере ещё нет MCP API (HTTP 404). Требуется обновить и перезапустить мобильный сервер."
    error is BridgeException && error.status == 401 -> "Сервер отклонил доступ устройства (HTTP 401). Проверьте подключение."
    error is BridgeException -> "Ошибка мобильного сервера: HTTP ${error.status}."
    else -> "Нет соединения с MCP. Проверьте доступность мобильного сервера."
}

internal fun mcpConnectionLabel(entry: JsonObject): String =
    entry["server"]!!.jsonPrimitive.content +
        (if (entry["readonly"]?.jsonPrimitive?.booleanOrNull == true) " · только чтение" else "") +
        " · " + entry["profile"]!!.jsonPrimitive.content +
        (if (entry["local"]?.jsonPrimitive?.booleanOrNull == true) " · локально" else " · удалённая модель")

@Composable
fun McpTaskDialog(onDismiss: () -> Unit, initialManage: Boolean = false, onSelect: (JsonObject) -> Unit = {}) {
    val screenshotsBlocked by Bridge.screenshotsBlocked.collectAsState()
    var managing by remember { mutableStateOf(initialManage) }
    var refresh by remember { mutableStateOf(0) }
    var entries by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var status by remember { mutableStateOf("Загружаю подключения…") }
    LaunchedEffect(refresh) {
        try {
            val reply = Bridge.mcpRequest("list")
            val error = reply["error"]?.jsonPrimitive?.contentOrNull
            if (error != null) status = mcpFailure(error)
            else {
                entries = reply["entries"]?.jsonArray?.map { it.jsonObject }.orEmpty()
                status = if (entries.isEmpty()) "Нет разрешённых подключений. Нажмите «Настроить доступ» и выберите MCP и профиль."
                    else "Выберите подключение. Затем пишите в обычном поле сообщения — ответы появятся в чате."
            }
        } catch (error: Exception) { status = mcpFailure(error) }
    }
    if (managing) {
        McpPermissionsDialog(onClose = {
            if (initialManage) onDismiss() else { managing = false; refresh++ }
        }, screenshotsBlocked = screenshotsBlocked)
        return
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(securePolicy = if (screenshotsBlocked) SecureFlagPolicy.SecureOn else SecureFlagPolicy.SecureOff),
        title = { Text("Подключения MCP") },
        text = {
            Column(Modifier.fillMaxWidth().heightIn(max = 560.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                TextButton(onClick = { managing = true }) { Text("Настроить доступ") }
                entries.forEach { entry ->
                    AssistChip(onClick = { onSelect(entry); onDismiss() }, label = { Text(mcpConnectionLabel(entry)) })
                }
                Text(status, style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть") } },
    )
}
