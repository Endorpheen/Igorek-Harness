package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.SecureFlagPolicy
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import ru.igorek.remote.data.Bridge

@Composable
internal fun McpPermissionsDialog(onClose: () -> Unit, screenshotsBlocked: Boolean) {
    var catalog by remember { mutableStateOf<JsonObject?>(null) }
    var server by remember { mutableStateOf("") }
    var profile by remember { mutableStateOf<JsonObject?>(null) }
    var access by remember { mutableStateOf("deny") }
    var status by remember { mutableStateOf("Загружаю подключения этого сервера…") }
    var busy by remember { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val modes = listOf("deny" to "Запрещён", "local_only" to "Только локальная модель", "allow" to "Разрешить этому профилю")
    val cid = profile?.get("credential_id")?.jsonPrimitive?.contentOrNull.orEmpty()
    val local = profile?.get("local")?.jsonPrimitive?.booleanOrNull == true
    val name = profile?.get("name")?.jsonPrimitive?.contentOrNull.orEmpty()
    val properties = DialogProperties(securePolicy = if (screenshotsBlocked) SecureFlagPolicy.SecureOn else SecureFlagPolicy.SecureOff)
    LaunchedEffect(Unit) {
        try {
            val reply = Bridge.mcpRequest("catalog")
            val error = reply["error"]?.jsonPrimitive?.contentOrNull
            if (error != null) status = mcpFailure(error)
            else { catalog = reply; status = "Выберите подключение, профиль модели и его права." }
        } catch (error: Exception) { status = mcpFailure(error) }
    }
    LaunchedEffect(server, cid) {
        access = catalog?.get("permissions")?.jsonObject?.get(server)?.jsonObject?.get(cid)?.jsonPrimitive?.contentOrNull ?: "deny"
    }
    if (confirming) {
        AlertDialog(
            properties = properties,
            onDismissRequest = { if (!busy) confirming = false },
            title = { Text("Подтвердите права MCP") },
            text = { Text("$server → $name\n${modes.first { it.first == access }.second}\n\n" +
                if (access == "deny") "Новые вызовы этого подключения будут запрещены."
                else if (local) "Данные будут обрабатываться выбранной моделью на локальном адресе сервера."
                else "Данные этого MCP, включая выбранные письма Gmail, будут передаваться удалённой модели этого профиля.") },
            confirmButton = {
                Button(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        try {
                            val reply = Bridge.mcpRequest("configure_device", buildJsonObject {
                                put("server", server); put("credential_id", cid); put("access", access)
                                put("confirmation", "owner_confirmed")
                            })
                            val error = reply["error"]?.jsonPrimitive?.contentOrNull
                            if (error != null) status = mcpFailure(error)
                            else {
                                catalog = Bridge.mcpRequest("catalog")
                                status = "Права сохранены. Вернитесь к подключениям и выберите разрешённый профиль."
                            }
                        } catch (error: Exception) { status = mcpFailure(error) }
                        finally { busy = false; confirming = false }
                    }
                }) { Text("Подтвердить") }
            },
            dismissButton = { TextButton(enabled = !busy, onClick = { confirming = false }) { Text("Отмена") } },
        )
        return
    }
    AlertDialog(
        properties = properties,
        onDismissRequest = onClose,
        title = { Text("Доступ к MCP") },
        text = {
            Column(Modifier.fillMaxWidth().heightIn(max = 560.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Подключение", style = MaterialTheme.typography.titleSmall)
                catalog?.get("servers")?.jsonArray?.forEach { value ->
                    val entry = value.jsonObject
                    val id = entry["id"]!!.jsonPrimitive.content
                    val disabled = entry["disabled"]?.jsonPrimitive?.booleanOrNull == true
                    FilterChip(selected = server == id, enabled = !disabled,
                        onClick = { server = id }, label = { Text(if (id == "gmail") "Gmail · только чтение" else id) })
                }
                if (server == "gmail" && catalog?.get("gmail_connected")?.jsonPrimitive?.booleanOrNull != true)
                    Text("Gmail ещё не подключён к этому серверу.", color = MaterialTheme.colorScheme.error)
                Text("Профиль модели", style = MaterialTheme.typography.titleSmall)
                catalog?.get("profiles")?.jsonArray?.forEach { value ->
                    val entry = value.jsonObject
                    val id = entry["credential_id"]!!.jsonPrimitive.content
                    val label = entry["name"]!!.jsonPrimitive.content + if (entry["local"]?.jsonPrimitive?.booleanOrNull == true) " · локально" else " · удалённая модель"
                    FilterChip(selected = cid == id, onClick = { profile = entry }, label = { Text(label) })
                }
                if (catalog?.get("profiles")?.jsonArray?.isEmpty() == true) Text("На этом сервере пока нет совместимых профилей модели.")
                Text("Разрешение", style = MaterialTheme.typography.titleSmall)
                modes.forEach { (mode, label) ->
                    FilterChip(selected = access == mode, enabled = mode != "local_only" || local,
                        onClick = { access = mode }, label = { Text(label) })
                }
                Text(status, style = MaterialTheme.typography.bodySmall)
                Button(enabled = server.isNotBlank() && cid.isNotBlank() && (access != "local_only" || local),
                    onClick = { confirming = true }) { Text("Сохранить выбранный доступ") }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("К подключениям") } },
    )
}
