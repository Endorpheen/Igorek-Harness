package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.LocalChatCommand

@Composable
fun LocalCommandDialog(command: LocalChatCommand, status: List<Pair<String, String>>, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text(if (command == LocalChatCommand.Help) uiText("Команды чата", "Chat commands") else uiText("Статус", "Status")) },
        text = {
            Column(Modifier.fillMaxWidth().heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                val lines = if (command == LocalChatCommand.Help) listOf(
                    "/help" to uiText("Показать эту справку", "Show this help"),
                    "/status" to uiText("Показать данные чата и контекста", "Show chat and context details"),
                    "/goal" to uiText("Задать или изменить цель чата", "Set or edit the chat goal"),
                    uiText("План", "Plan") to uiText("Кнопка рядом со статусом включает и выключает режим плана", "The button next to the status toggles plan mode"),
                ) else status
                SelectionContainer {
                    Column {
                        lines.forEach { (label, value) ->
                            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.padding(top = 6.dp))
                            Text(value, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(uiText("Закрыть", "Close")) } })
}
