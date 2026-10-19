package ru.igorek.remote.ui

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.EditNote
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.IconButton
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.SecureFlagPolicy
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.ApprovalDto
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.ui.markdown.MarkdownText

private val Mono = FontFamily.Monospace
internal val Success = Color(0xFF44D7A8)
internal val Warning = Color(0xFFF6B94A)
internal val Danger = Color(0xFFFF667A)
internal val Info = Color(0xFF5EA7FF)

@Composable
private fun Label(text: String, color: Color) {
    Box(
        modifier = Modifier
            .background(color.copy(alpha = 0.16f), RoundedCornerShape(7.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp)
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

@Composable
private fun ApprovalActions(busy: Boolean, onDecide: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = { onDecide("reject") },
                enabled = !busy,
                modifier = Modifier.weight(1f),
                border = BorderStroke(1.dp, Danger.copy(alpha = 0.8f)),
            ) {
                Text(uiText("Запретить", "Deny"), color = Danger)
            }
            Button(
                onClick = { onDecide("allow_once") },
                enabled = !busy,
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
            ) {
                Text(if (busy) uiText("Отправляем…", "Sending…") else uiText("Разрешить раз", "Allow once"))
            }
        }
        TextButton(onClick = { onDecide("allow_session") }, enabled = !busy) {
            Text(uiText("Разрешить до конца сессии", "Allow for this session"))
        }
    }
}

private fun toolAccent(status: String): Color = when (status) {
    "running" -> Info
    "error", "rejected" -> Danger
    "exit" -> Warning
    "deferred" -> Warning
    else -> Success
}

private fun toolIcon(name: String): ImageVector = when (name.lowercase()) {
    "bash", "shell", "exec", "executecommand" -> Icons.Default.Terminal
    "read" -> Icons.Default.Description
    "write", "edit" -> Icons.Default.EditNote
    else -> Icons.Default.Build
}

internal fun formatDuration(durationMs: Long): String = when {
    durationMs <= 0 -> ""
    durationMs < 1000 -> AppLocale.text("$durationMs мс", "$durationMs ms")
    else -> AppLocale.text(
        String.format(java.util.Locale.US, "%.1f с", durationMs / 1000.0),
        String.format(java.util.Locale.US, "%.1f s", durationMs / 1000.0),
    )
}

internal fun toolStatusLabel(status: String): String = when (status.lowercase()) {
    "running" -> AppLocale.text("работает", "running")
    "done", "ok" -> AppLocale.text("готово", "done")
    "error", "fail" -> AppLocale.text("ошибка", "error")
    "ожидает решения", "waiting_approval" -> AppLocale.text("ожидает решения", "waiting for approval")
    "allowed" -> AppLocale.text("разрешено", "allowed")
    "rejected" -> AppLocale.text("отклонено", "rejected")
    "deferred" -> AppLocale.text("не выполнено", "not executed")
    else -> status
}

private fun approvalDecisionLabel(decision: String): String = when (decision.lowercase()) {
    "reject", "deny" -> AppLocale.text("запрещено", "denied")
    "allow_once" -> AppLocale.text("разрешено один раз", "allowed once")
    "allow_session" -> AppLocale.text("разрешено для сессии", "allowed for session")
    else -> decision
}

@Composable
fun ToolCallCard(item: TimelineItem.ToolCall, modifier: Modifier = Modifier) {
    val accent = toolAccent(item.status)
    // An active (still executing) card receives a glowing border along its perimeter.
    val running = isRunningToolStatus(item.status)
    val shape = RoundedCornerShape(15.dp)
    val runningBorder = if (running) {
        Modifier.runningToolBorder(shape = shape, color = accent, phase = rememberToolBorderPhase())
    } else {
        Modifier
    }
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    val copyText = buildString {
        append(item.name).append('\n')
        if (item.input.isNotBlank()) append(item.input).append('\n')
        if (item.resultPreview.isNotBlank()) append(item.resultPreview)
    }
    Card(
        modifier = modifier.fillMaxWidth().animateContentSize().then(runningBorder),
        shape = shape,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        border = BorderStroke(1.dp, accent.copy(alpha = if (running) 0.38f else 0.24f)),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Row(
                Modifier.fillMaxWidth().clickable { expanded = !expanded }.padding(start = 11.dp, top = 9.dp, end = 5.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    modifier = Modifier.size(34.dp),
                    shape = RoundedCornerShape(10.dp),
                    color = accent.copy(alpha = 0.13f),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(toolIcon(item.name), contentDescription = null, modifier = Modifier.size(19.dp), tint = accent)
                    }
                }
                Text(
                    item.name,
                    modifier = Modifier.padding(start = 9.dp).weight(1f),
                    fontWeight = FontWeight.SemiBold,
                    color = accent,
                )
                Label(toolStatusLabel(item.status), accent)
                if (item.durationMs > 0) {
                    Box(Modifier.padding(start = 5.dp)) {
                        Label(formatDuration(item.durationMs), MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                CopyButton(copyText)
                Icon(
                    if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = if (expanded) uiText("Свернуть", "Collapse") else uiText("Раскрыть", "Expand"),
                    modifier = Modifier.size(20.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            SelectionContainer {
                Column(
                    Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, bottom = 11.dp),
                    verticalArrangement = Arrangement.spacedBy(7.dp),
                ) {
                    if (item.input.isNotBlank()) {
                        Text(
                            item.input,
                            fontFamily = Mono,
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = if (expanded) Int.MAX_VALUE else 3,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    if (item.resultPreview.isNotBlank()) {
                        Surface(
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(10.dp),
                            color = MaterialTheme.colorScheme.background.copy(alpha = 0.55f),
                        ) {
                            Text(
                                item.resultPreview,
                                modifier = Modifier.padding(9.dp),
                                fontFamily = Mono,
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = if (expanded) Int.MAX_VALUE else 4,
                                overflow = TextOverflow.Ellipsis,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ApprovalCard(
    approval: ApprovalDto,
    onDecide: (String) -> Unit,
    busy: Boolean,
    modifier: Modifier = Modifier,
    onSudo: (String) -> Unit = {},
) {
    if (approval.kind == "sudo") {
        SudoApprovalCard(approval, onDecide, onSudo, busy, modifier)
        return
    }
    ApprovalSurface(
        dangerous = approval.dangerous,
        tool = approval.tool,
        target = approval.target.ifBlank { approval.input?.toString().orEmpty() },
        reason = approval.reason,
        resolved = null,
        busy = busy,
        modifier = modifier,
        decisionKey = "${approval.taskId}-${approval.requestId}",
        onDecide = onDecide,
    )
}

@Composable
private fun SudoApprovalCard(
    approval: ApprovalDto,
    onDecide: (String) -> Unit,
    onSudo: (String) -> Unit,
    busy: Boolean,
    modifier: Modifier,
) {
    var showPassword by remember(approval.requestId) { mutableStateOf(false) }
    var password by remember(approval.requestId) { mutableStateOf("") }
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
        border = BorderStroke(1.dp, Danger),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(uiText("Запрос прав администратора", "Administrator request"), fontWeight = FontWeight.Bold)
            Text(approval.target, fontFamily = Mono, style = MaterialTheme.typography.bodySmall)
            Text(approval.reason, style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { onDecide("reject") }, enabled = !busy) {
                    Text(uiText("Отклонить", "Reject"))
                }
                Button(onClick = { showPassword = true }, enabled = !busy) {
                    Text(uiText("Подтвердить sudo", "Approve sudo"))
                }
            }
        }
    }
    if (showPassword) {
        AlertDialog(
            onDismissRequest = { password = ""; showPassword = false },
            title = { Text(uiText("Пароль администратора", "Administrator password")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(approval.target, fontFamily = Mono, style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text(uiText("Пароль sudo", "Sudo password")) },
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        singleLine = true,
                    )
                }
            },
            confirmButton = {
                Button(onClick = {
                    val submitted = password
                    password = ""
                    showPassword = false
                    onSudo(submitted)
                }, enabled = password.isNotEmpty() && !busy) {
                    Text(uiText("Выполнить", "Run"))
                }
            },
            dismissButton = {
                OutlinedButton(onClick = { password = ""; showPassword = false }) {
                    Text(uiText("Отмена", "Cancel"))
                }
            },
            properties = DialogProperties(securePolicy = SecureFlagPolicy.Inherit),
        )
    }
}

@Composable
private fun ApprovalSurface(
    dangerous: Boolean,
    tool: String,
    target: String,
    reason: String,
    resolved: String?,
    busy: Boolean,
    modifier: Modifier,
    onDecide: (String) -> Unit,
    decisionKey: String = "",
) {
    val accent = if (dangerous) Danger else Warning
    // While a decision is pending, a glow runs along the perimeter of the confirmation window—same as the executing tool’s, but with the accent color of the window (red for dangerous requests).
    var decidedLocally by rememberSaveable(decisionKey) { mutableStateOf(false) }
    val tracePhase = rememberToolBorderPhase(durationMillis = 1500)
    val traceActive = isApprovalTraceActive(resolved, decidedLocally)
    Card(
        modifier = modifier
            .fillMaxWidth()
            .then(
                if (traceActive) {
                    Modifier.runningToolBorder(color = accent, phase = tracePhase, stroke = 1.8.dp)
                } else {
                    Modifier
                }
            ),
        shape = RoundedCornerShape(15.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (dangerous) MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.72f)
            else Warning.copy(alpha = 0.08f),
        ),
        border = BorderStroke(1.dp, accent.copy(alpha = 0.55f)),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Default.WarningAmber, contentDescription = null, tint = accent)
                Label(if (dangerous) uiText("ОПАСНО", "DANGEROUS") else uiText("ПОДТВЕРЖДЕНИЕ", "APPROVAL"), accent)
                Text(tool, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                resolved?.let { Label(approvalDecisionLabel(it), MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            SelectionContainer {
                Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    if (target.isNotBlank()) {
                        Box(Modifier.fillMaxWidth().heightIn(max = 240.dp).verticalScroll(rememberScrollState())) {
                            Text(target, fontFamily = Mono, style = MaterialTheme.typography.bodySmall)
                        }
                        CopyButton(target)
                    }
                    if (reason.isNotBlank()) {
                        Text(reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (resolved == null) {
                ApprovalActions(busy) { decision ->
                    decidedLocally = true
                    onDecide(decision)
                }
            }
        }
    }
}

@Composable
private fun UserSkillBadges(skills: List<String>) {
    if (skills.isEmpty()) return
    Row(
        Modifier.fillMaxWidth().padding(start = 26.dp, top = 7.dp).horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        skills.forEach { skill ->
            Surface(
                shape = RoundedCornerShape(9.dp),
                color = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.5f)),
            ) {
                Row(
                    Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Icon(
                        Icons.Default.Extension,
                        contentDescription = null,
                        modifier = Modifier.size(12.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Text(
                        "\$$skill",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 1,
                    )
                }
            }
        }
    }
}

@Composable
fun TimelineRow(
    item: TimelineItem,
    modifier: Modifier = Modifier,
    busyRequestId: String? = null,
    onDecide: (requestId: String, taskId: String, decision: String) -> Unit = { _, _, _ -> },
    onSendNow: (taskId: String, messageId: String) -> Unit = { _, _ -> },
    onCancelMessage: (taskId: String, messageId: String) -> Unit = { _, _ -> },
    sessionId: String = "",
) {
    when (item) {
        is TimelineItem.UserText -> Card(
            modifier = modifier.fillMaxWidth(),
            shape = RoundedCornerShape(15.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.55f)),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.2f)),
        ) {
            Column(Modifier.fillMaxWidth().padding(start = 12.dp, top = 7.dp, end = 5.dp, bottom = 7.dp)) {
                Row(Modifier.fillMaxWidth()) {
                    Icon(Icons.Default.Person, contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                    if (item.displayText.isNotBlank()) {
                        SelectionContainer(Modifier.padding(start = 8.dp).weight(1f)) {
                            Text(item.displayText, style = MaterialTheme.typography.bodyMedium)
                        }
                        CopyButton(item.displayText)
                    }
                }
                UserSkillBadges(item.skills)
                MessageUploadAttachments(sessionId, item.attachments)
                if (item.queued || item.delivered) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(start = 26.dp, top = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        if (item.delivered) {
                            Icon(
                                Icons.Default.CheckCircle,
                                contentDescription = null,
                                modifier = Modifier.size(13.dp),
                                tint = MaterialTheme.colorScheme.primary,
                            )
                        }
                        Text(
                            when {
                                item.delivered -> uiText("Передано Игорьку", "Delivered to Igorek")
                                item.prioritized -> uiText(
                                    "Передам Игорьку после ближайшего действия…",
                                    "Sending to Igorek after the next action…",
                                )
                                else -> uiText("В очереди", "Queued")
                            },
                            modifier = Modifier.weight(1f).padding(start = if (item.delivered) 4.dp else 0.dp),
                            style = MaterialTheme.typography.labelSmall,
                            color = if (item.delivered) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        if (item.queued) {
                            var menuExpanded by remember(item.id) { mutableStateOf(false) }
                            Box {
                                IconButton(
                                    onClick = { menuExpanded = true },
                                    modifier = Modifier.size(32.dp),
                                ) {
                                    Icon(
                                        Icons.Default.MoreVert,
                                        contentDescription = uiText("Действия с сообщением", "Message actions"),
                                        modifier = Modifier.size(20.dp),
                                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
                                    DropdownMenuItem(
                                        text = { Text(uiText("Отправить сейчас", "Send now")) },
                                        enabled = !item.prioritized && item.taskId.isNotBlank() && item.messageId.isNotBlank(),
                                        onClick = {
                                            menuExpanded = false
                                            onSendNow(item.taskId, item.messageId)
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = {
                                            Text(
                                                uiText("Отменить сообщение", "Cancel message"),
                                                color = MaterialTheme.colorScheme.error,
                                            )
                                        },
                                        enabled = item.taskId.isNotBlank() && item.messageId.isNotBlank(),
                                        onClick = {
                                            menuExpanded = false
                                            onCancelMessage(item.taskId, item.messageId)
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        is TimelineItem.AssistantText -> Card(
            modifier = modifier.fillMaxWidth(),
            shape = RoundedCornerShape(15.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant,
                contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
            ),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.24f)),
        ) {
            Column(Modifier.fillMaxWidth().padding(start = 12.dp, top = 9.dp, end = 5.dp, bottom = 11.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.AutoAwesome, contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                    Text(
                        uiText("Ответ Игорька", "Igorek's response"),
                        modifier = Modifier.padding(start = 7.dp).weight(1f),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    CopyButton(item.text)
                }
                SelectionContainer {
                    MarkdownText(item.text + if (item.streaming) " ▌" else "")
                }
                if (sessionId.isNotBlank()) item.files.forEach { file ->
                    AttachmentCard(sessionId, file)
                }
            }
        }

        is TimelineItem.Reasoning -> ReasoningCard(item, modifier)
        is TimelineItem.ToolCall -> ToolCallCard(item, modifier)
        is TimelineItem.ApprovalNotice -> ApprovalSurface(
            dangerous = item.dangerous,
            tool = item.tool,
            target = item.target.ifBlank { item.input },
            reason = item.reason,
            resolved = item.resolved,
            busy = busyRequestId == item.requestId,
            modifier = modifier,
            decisionKey = "${item.taskId}-${item.requestId}",
            onDecide = { decision -> onDecide(item.requestId, item.taskId, decision) },
        )
        is TimelineItem.Status -> StatusCard(item, modifier)
        is TimelineItem.Compaction -> CompactionCard(item, modifier)
    }
}

@Composable
private fun CompactionCard(item: TimelineItem.Compaction, modifier: Modifier) {
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    val title = when (item.phase) {
        "started" -> uiText("Сжимаем контекст", "Compacting context")
        "progress" -> uiText("Готовим память для продолжения", "Preparing session memory")
        "completed" -> uiText("Контекст сжат", "Context compacted")
        else -> uiText("Сжатие не удалось — история сохранена", "Compaction failed — history preserved")
    }
    Card(modifier = modifier.fillMaxWidth().clickable { expanded = !expanded }.animateContentSize()) {
        Column(Modifier.padding(12.dp)) {
            Text(title + if (item.chunk > 0) " · ${item.chunk}/${item.totalChunks}" else "", style = MaterialTheme.typography.titleSmall)
            Text(uiText("Нажмите, чтобы посмотреть переносимую память", "Tap to inspect carried memory"), style = MaterialTheme.typography.labelSmall)
            if (expanded) {
                Text(if (item.phase == "completed") uiText("Эта память передана агенту вместе с последними ходами.", "This memory is sent to the agent with recent turns.")
                    else uiText("До успешного сохранения контекст остаётся прежним.", "Context remains unchanged until successfully saved."))
                SelectionContainer { MarkdownText(item.summary.ifBlank { item.reason }) }
            }
        }
    }
}

@Composable
private fun StatusCard(item: TimelineItem.Status, modifier: Modifier) {
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    val color = when (item.level) {
        "error" -> Danger
        "warn" -> Warning
        "ok" -> Success
        else -> Info
    }
    val icon = if (item.level == "ok") Icons.Default.CheckCircle else Icons.Default.ErrorOutline
    Card(
        modifier = modifier
            .fillMaxWidth()
            .then(
                if (item.details.isNotBlank()) {
                    Modifier.clickable { expanded = !expanded }.animateContentSize()
                } else {
                    Modifier
                }
            ),
        shape = RoundedCornerShape(13.dp),
        colors = CardDefaults.cardColors(containerColor = color.copy(alpha = 0.09f)),
        border = BorderStroke(1.dp, color.copy(alpha = 0.32f)),
    ) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                Icon(icon, contentDescription = null, modifier = Modifier.size(20.dp), tint = color)
                Text(item.text, modifier = Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, color = color)
            }
            if (item.details.isNotBlank()) {
                Text(
                    if (expanded) uiText("Скрыть подробности", "Hide details")
                    else uiText("Нажмите, чтобы увидеть причину", "Tap to see the reason"),
                    modifier = Modifier.padding(start = 29.dp, top = 4.dp),
                    style = MaterialTheme.typography.labelSmall,
                    color = color,
                )
                if (expanded) {
                    SelectionContainer {
                        Text(
                            item.details,
                            modifier = Modifier.padding(start = 29.dp, top = 6.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ReasoningCard(item: TimelineItem.Reasoning, modifier: Modifier = Modifier) {
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    Card(
        modifier = modifier.fillMaxWidth().clickable { expanded = !expanded }.animateContentSize(),
        shape = RoundedCornerShape(15.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF102A32)),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.secondary.copy(alpha = 0.35f)),
    ) {
        Column(Modifier.padding(11.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Psychology, contentDescription = null, modifier = Modifier.size(20.dp), tint = MaterialTheme.colorScheme.secondary)
                Text(
                    uiText("размышления", "reasoning"),
                    modifier = Modifier.padding(start = 7.dp),
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.secondary,
                )
                Spacer(Modifier.weight(1f))
                Text(
                    if (expanded) uiText("свернуть", "collapse") else uiText("показать полностью", "show all"),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.secondary,
                )
                CopyButton(item.text)
            }
            SelectionContainer {
                Text(
                    item.text,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = if (expanded) Int.MAX_VALUE else 3,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}
