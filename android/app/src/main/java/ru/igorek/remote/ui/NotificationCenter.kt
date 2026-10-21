package ru.igorek.remote.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.NotificationEntry
import ru.igorek.remote.data.visibleNotifications
import ru.igorek.remote.data.unreadNotificationCount

@Composable
internal fun NotificationCenterDialog(entries: List<NotificationEntry>, onClose: () -> Unit,
    onRead: () -> Unit, onClear: () -> Unit, onSupport: () -> Unit) {
AlertDialog(
            onDismissRequest = { onClose() },
            title = { Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween) {
                Text(uiText("Уведомления", "Notifications"))
                IconButton(onClick={onSupport()}) {
                    Icon(Icons.AutoMirrored.Filled.Send,contentDescription=uiText("Отправить диагностику", "Send diagnostics"))
                }
            } },
            text = {
                LazyColumn(
                    Modifier.heightIn(max = 420.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (entries.isEmpty()) {
                        item { Text(uiText("Нет активных уведомлений об ошибках", "No active error notifications")) }
                    } else {
                        items(entries, key = { it.id }) { entry ->
                            NotificationCard(entry)
                        }
                    }
                }
            },
            confirmButton = {
                // Three buttons evenly divide the row; labels are centered and do not wrap:
                // otherwise, the «Close» button will split into two lines, and the last character will drop down.
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    TextButton(
                        onClick = { onRead() },
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 4.dp, vertical = 8.dp),
                    ) {
                        Text(
                            uiText("Прочитать", "Mark read"),
                            maxLines = 1,
                            softWrap = false,
                            textAlign = TextAlign.Center,
                        )
                    }
                    TextButton(
                        onClick = {
                            onClear()
                        },
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 4.dp, vertical = 8.dp),
                    ) {
                        Text(
                            uiText("Очистить", "Clear"),
                            maxLines = 1,
                            softWrap = false,
                            textAlign = TextAlign.Center,
                        )
                    }
                    TextButton(
                        onClick = { onClose() },
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 4.dp, vertical = 8.dp),
                    ) {
                        Text(
                            uiText("Закрыть", "Close"),
                            maxLines = 1,
                            softWrap = false,
                            textAlign = TextAlign.Center,
                        )
                    }
                }
            },
        )
}

@Composable
internal fun NotificationCenterButton() {
    val notifications by Bridge.notifications.collectAsState()
    val entries = visibleNotifications(notifications).sortedByDescending { it.lastSeen }
    var open by remember { mutableStateOf(false) }
    var support by remember { mutableStateOf(false) }
    IconButton(onClick = { open = true }) {
        BadgedBox(badge = { val count = unreadNotificationCount(entries); if (count > 0) Badge { Text(count.toString()) } }) {
            Icon(Icons.Default.Notifications, uiText("Уведомления", "Notifications"))
        }
    }
    if (open) NotificationCenterDialog(entries,
        onClose = { Bridge.markNotificationsRead(); open = false },
        onRead = Bridge::markNotificationsRead, onClear = Bridge::clearNotifications,
        onSupport = { open = false; support = true })
    if (support) DiagnosticSupportDialog { support = false; open = true }
}
