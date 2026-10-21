@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package ru.igorek.remote.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ExternalMessageDto
import ru.igorek.remote.data.ExternalSessionDetail
import ru.igorek.remote.data.ExternalSessionSummary
import ru.igorek.remote.data.SessionOptionsResponse
import ru.igorek.remote.ui.markdown.MarkdownText
import java.text.DateFormat
import java.util.Date
import java.util.UUID
import kotlinx.coroutines.launch

private data class SourceChoice(val id: String, val russian: String, val english: String)

private val sourceChoices = listOf(
    SourceChoice("", "Все", "All"),
    SourceChoice("claude", "Claude", "Claude"),
    SourceChoice("opencode", "OpenCode", "OpenCode"),
    SourceChoice("codex", "Codex", "Codex"),
)

private fun sourceTitle(source: String): String = when (source) {
    "claude" -> "Claude Code"
    "opencode" -> "OpenCode"
    "codex" -> "Codex"
    else -> source
}

private fun externalDate(epochSeconds: Long): String =
    if (epochSeconds <= 0) "" else DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        .format(Date(epochSeconds * 1000))

/**
 * Quick navigation buttons for history: up moves to the start of the list, down moves to the end.
 * Visually and in behavior, they match native sessions (SessionScreen).
 */
@Composable
private fun ScrollJumpButtons(listState: LazyListState, modifier: Modifier = Modifier) {
    val scope = rememberCoroutineScope()
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(8.dp),
        horizontalAlignment = Alignment.End,
    ) {
        AnimatedVisibility(
            visible = listState.canScrollBackward,
            enter = fadeIn() + scaleIn(),
            exit = fadeOut() + scaleOut(),
        ) {
            SmallFloatingActionButton(
                onClick = { scope.launch { listState.scrollToItem(0) } },
                containerColor = MaterialTheme.colorScheme.surfaceVariant,
                contentColor = MaterialTheme.colorScheme.primary,
            ) {
                Icon(
                    Icons.Default.ArrowUpward,
                    contentDescription = uiText("В начало истории", "Jump to top"),
                )
            }
        }
        AnimatedVisibility(
            visible = listState.canScrollForward,
            enter = fadeIn() + scaleIn(),
            exit = fadeOut() + scaleOut(),
        ) {
            SmallFloatingActionButton(
                onClick = {
                    scope.launch {
                        val lastItem = listState.layoutInfo.totalItemsCount - 1
                        if (lastItem >= 0) listState.scrollToItem(lastItem)
                    }
                },
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            ) {
                Icon(
                    Icons.Default.ArrowDownward,
                    contentDescription = uiText("В конец истории", "Jump to bottom"),
                )
            }
        }
    }
}

@Composable
fun ExternalSessionsScreen(
    onMenu: () -> Unit,
    onOpenImported: (String) -> Unit,
) {
    val catalog by Bridge.externalSessions.collectAsState()
    val detail by Bridge.externalSessionDetail.collectAsState()
    val lastError by Bridge.lastError.collectAsState()
    val connection by Bridge.connection.collectAsState()
    var selectedSource by rememberSaveable { mutableStateOf("") }
    var query by rememberSaveable { mutableStateOf("") }
    var opening by remember { mutableStateOf<ExternalSessionSummary?>(null) }
    var loading by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    LaunchedEffect(Unit) {
        loading = true
        Bridge.refreshExternalSessions { loading = false }
    }

    val opened = detail?.takeIf {
        it.source == opening?.source && it.sessionId == opening?.sessionId
    }
    if (opening != null) {
        ExternalSessionDetailView(
            summary = opening!!,
            detail = opened,
            error = lastError,
            onBack = {
                opening = null
                Bridge.closeExternalSession()
            },
            onRetry = {
                Bridge.openExternalSession(opening!!.source, opening!!.sessionId)
            },
            onOpenImported = onOpenImported,
        )
        return
    }

    val needle = query.trim().lowercase()
    val visible = catalog.sessions.filter { session ->
        (selectedSource.isBlank() || session.source == selectedSource) &&
            (needle.isBlank() || listOf(
                session.title,
                session.cwd,
                session.model,
                session.agent,
                session.sessionId,
            ).any { it.lowercase().contains(needle) })
    }

    Scaffold(
        topBar = {
            ControlTopBar(
                title = uiText("История агентов", "Agent history"),
                subtitle = uiText(
                    if (connection == "online") "Claude Code, OpenCode и Codex" else "Восстанавливаем связь…",
                    if (connection == "online") "Claude Code, OpenCode, and Codex" else "Reconnecting…",
                ),
                connection = connection,
                onMenu = onMenu,
                actions = {
                    IconButton(onClick = {
                        loading = true
                        Bridge.refreshExternalSessions(refresh = true) { loading = false }
                    }) {
                        Icon(Icons.Default.Refresh, contentDescription = uiText("Сканировать заново", "Rescan"))
                    }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                label = { Text(uiText("Поиск по истории", "Search history")) },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
            )
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(sourceChoices, key = { it.id }) { choice ->
                    val count = if (choice.id.isBlank()) catalog.total else catalog.counts[choice.id] ?: 0
                    FilterChip(
                        selected = selectedSource == choice.id,
                        onClick = { selectedSource = choice.id },
                        label = {
                            Text("${uiText(choice.russian, choice.english)} · $count")
                        },
                    )
                }
            }
            when {
                loading && catalog.sessions.isEmpty() -> Row(
                    Modifier.fillMaxWidth().padding(24.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(Modifier.padding(end = 12.dp))
                    Text(uiText("Сканируем сохранённые сессии…", "Scanning saved sessions…"))
                }
                visible.isEmpty() -> Text(
                    uiText("Подходящих сессий не найдено.", "No matching sessions found."),
                    modifier = Modifier.padding(vertical = 20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                else -> Box(Modifier.fillMaxWidth().weight(1f)) {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(bottom = 18.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(visible, key = { "${it.source}:${it.sessionId}" }) { session ->
                            ExternalSessionCard(session) {
                                opening = session
                                Bridge.openExternalSession(session.source, session.sessionId)
                            }
                        }
                    }
                    ScrollJumpButtons(
                        listState = listState,
                        modifier = Modifier.align(Alignment.BottomEnd).padding(end = 12.dp, bottom = 12.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun ExternalSessionCard(session: ExternalSessionSummary, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    sourceTitle(session.source),
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(externalDate(session.updatedAt), style = MaterialTheme.typography.labelSmall)
            }
            Text(
                session.title.ifBlank { session.sessionId },
                style = MaterialTheme.typography.titleMedium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            if (session.cwd.isNotBlank()) {
                Text(
                    session.cwd,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            val metadata = listOfNotNull(
                session.model.takeIf { it.isNotBlank() },
                session.agent.takeIf { it.isNotBlank() },
                session.messageCount.takeIf { it > 0 }?.let {
                    uiText("$it сообщений", "$it messages")
                },
            ).joinToString(" · ")
            if (metadata.isNotBlank()) {
                Text(metadata, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun ExternalSessionDetailView(
    summary: ExternalSessionSummary,
    detail: ExternalSessionDetail?,
    error: String?,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onOpenImported: (String) -> Unit,
) {
    var showImport by remember { mutableStateOf(false) }
    var loadingMore by remember { mutableStateOf(false) }
    val detailListState = rememberLazyListState()
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(summary.title.ifBlank { sourceTitle(summary.source) }, maxLines = 1)
                        Text(sourceTitle(summary.source), style = MaterialTheme.typography.labelSmall)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = uiText("Назад", "Back"))
                    }
                },
                actions = {
                    IconButton(onClick = onRetry) {
                        Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить", "Refresh"))
                    }
                },
            )
        },
        bottomBar = {
            if (detail != null) {
                Column(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Button(onClick = { showImport = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(uiText("Создать продолжение в Игорьке", "Continue in Igorek"))
                    }
                    if (detail.nativeResume) {
                        Text(
                            uiText(
                                "Исходная среда поддерживает native resume; прямое подключение появится следующим этапом.",
                                "The source supports native resume; direct connection is planned for the next phase.",
                            ),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        },
    ) { padding ->
        when {
            detail == null && error == null -> Row(
                Modifier.fillMaxSize().padding(padding),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CircularProgressIndicator(Modifier.padding(end = 12.dp))
                Text(uiText("Загружаем историю…", "Loading history…"))
            }
            detail == null -> Column(
                Modifier.fillMaxSize().padding(padding).padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                ErrorNotificationEffect(error, "external-history")
                Button(onClick = onRetry) { Text(uiText("Повторить", "Retry")) }
            }
            else -> Box(Modifier.fillMaxSize().padding(padding)) {
                LazyColumn(
                    state = detailListState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(9.dp),
                ) {
                    item {
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            if (detail.cwd.isNotBlank()) {
                                Text(detail.cwd, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
                            }
                            val metadata = listOfNotNull(
                                detail.model.takeIf { it.isNotBlank() },
                                detail.agent.takeIf { it.isNotBlank() },
                                externalDate(detail.updatedAt).takeIf { it.isNotBlank() },
                            ).joinToString(" · ")
                            if (metadata.isNotBlank()) Text(metadata, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                    items(detail.messages.size) { index ->
                        ExternalMessageCard(detail.messages[index])
                    }
                    if (detail.nextOffset != null) {
                        item {
                            Button(
                                enabled = !loadingMore,
                                onClick = {
                                    loadingMore = true
                                    Bridge.loadMoreExternalSession { loadingMore = false }
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(
                                    if (loadingMore) uiText("Загружаем…", "Loading…")
                                    else uiText(
                                        "Загрузить ещё (${detail.messages.size} из ${detail.totalMessages})",
                                        "Load more (${detail.messages.size} of ${detail.totalMessages})",
                                    )
                                )
                            }
                        }
                    }
                }
                ScrollJumpButtons(
                    listState = detailListState,
                    modifier = Modifier.align(Alignment.BottomEnd).padding(end = 12.dp, bottom = 12.dp),
                )
            }
        }
    }
    if (showImport && detail != null) {
        ImportExternalSessionDialog(
            detail = detail,
            onDismiss = { showImport = false },
            onImported = {
                showImport = false
                onOpenImported(it)
            },
        )
    }
}

@Composable
private fun ExternalMessageCard(message: ExternalMessageDto) {
    var expanded by remember(message.text) { mutableStateOf(false) }
    val clipped = message.text.length > 4_000 && !expanded
    val shown = if (clipped) message.text.take(4_000) + "…" else message.text
    val title = when (message.role) {
        "user" -> uiText("Пользователь", "User")
        "assistant" -> uiText("Ответ агента", "Agent response")
        "reasoning" -> uiText("Размышления", "Reasoning")
        else -> uiText("Инструмент", "Tool")
    }
    val container = when (message.role) {
        "user" -> MaterialTheme.colorScheme.primaryContainer
        "assistant" -> MaterialTheme.colorScheme.surfaceVariant
        "reasoning" -> MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.55f)
        else -> MaterialTheme.colorScheme.tertiaryContainer.copy(alpha = 0.55f)
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = container),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
            if (message.role == "tool") {
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                    Text(shown, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                }
            } else {
                MarkdownText(shown)
            }
            if (message.text.length > 4_000) {
                TextButton(onClick = { expanded = !expanded }) {
                    Text(if (expanded) uiText("Свернуть", "Collapse") else uiText("Показать полностью", "Show all"))
                }
            }
        }
    }
}

@Composable
private fun ImportExternalSessionDialog(
    detail: ExternalSessionDetail,
    onDismiss: () -> Unit,
    onImported: (String) -> Unit,
) {
    var options by remember { mutableStateOf(SessionOptionsResponse()) }
    var profile by rememberSaveable { mutableStateOf("") }
    // Empty cwd delegates workspace selection to the server.
    var cwd by rememberSaveable { mutableStateOf("") }
    var cwdTouched by rememberSaveable { mutableStateOf(false) }
    var key by rememberSaveable { mutableStateOf(UUID.randomUUID().toString()) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var cwdNotice by remember { mutableStateOf<String?>(null) }
    var createdSessionId by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(detail.source, detail.sessionId) {
        Bridge.loadSessionOptions { loaded, failure ->
            loading = false
            if (loaded != null) {
                options = loaded
                profile = loaded.defaultProfile
            } else {
                error = failure?.message
            }
        }
    }

    val inheritedCwd = detail.cwd.trim()
    val shownCwd = if (cwdTouched) cwd else inheritedCwd.ifBlank { options.defaultCwd }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(uiText("Создать продолжение", "Create continuation")) },
        text = {
            Column(
                Modifier.widthIn(max = 420.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    uiText(
                        "Будет создан новый чат Игорька. Исходная сессия останется без изменений.",
                        "A new Igorek chat will be created. The source session will remain unchanged.",
                    )
                )
                if (loading) CircularProgressIndicator()
                SessionOptionField(
                    label = uiText("Профиль", "Profile"),
                    value = profile,
                    options = options.profiles,
                    enabled = !loading && !busy,
                    onValueChange = { profile = it },
                )
                SessionOptionField(
                    label = uiText("Рабочая папка", "Working folder"),
                    value = shownCwd,
                    options = options.workdirs,
                    enabled = !loading && !busy,
                    onValueChange = {
                        cwdTouched = true
                        cwd = it
                        cwdNotice = null
                    },
                )
                Text(
                    text = when {
                        cwdTouched -> uiText(
                            "Папка выбрана вручную",
                            "Folder chosen manually",
                        )
                        inheritedCwd.isNotBlank() -> uiText(
                            "Будет унаследована папка исходной сессии: $inheritedCwd",
                            "The source session folder will be inherited: $inheritedCwd",
                        )
                        else -> uiText(
                            "Папка исходной сессии неизвестна - будет использована папка сервера: " +
                                options.defaultCwd,
                            "The source session folder is unknown - the server folder will be used: " +
                                options.defaultCwd,
                        )
                    },
                    style = MaterialTheme.typography.bodySmall,
                )
                cwdNotice?.let { ErrorNotificationEffect(it, "external-workdir") }
                error?.let { ErrorNotificationEffect(it, "ExternalSessionsScreen") }
            }
        },
        confirmButton = {
            TextButton(
                enabled = !loading && !busy &&
                    (createdSessionId != null || (profile.isNotBlank() && shownCwd.isNotBlank())),
                onClick = {
                    val resumed = createdSessionId
                    if (resumed != null) {
                        onImported(resumed)
                    } else {
                        busy = true
                        error = null
                        cwdNotice = null
                        Bridge.importExternalSession(
                            detail.source,
                            detail.sessionId,
                            profile,
                            if (cwdTouched) cwd else "",
                            key,
                        ) { sessionId, response, failure ->
                            busy = false
                            if (sessionId == null) {
                                error = failure?.message
                            } else {
                                val reason = response?.cwdFallbackReason.orEmpty()
                                if (!cwdTouched && reason.isNotBlank()) {
                                    createdSessionId = sessionId
                                    cwdNotice = AppLocale.text(
                                        "Папка исходной сессии недоступна изоляции ($reason). " +
                                            "Сессия создана в ${response?.cwd.orEmpty()}. Нажмите «Продолжить».",
                                        "The source session folder is not allowed by isolation ($reason). " +
                                            "The session was created in ${response?.cwd.orEmpty()}. Press Continue.",
                                    )
                                } else {
                                    onImported(sessionId)
                                }
                            }
                        }
                    }
                },
            ) {
                Text(
                    when {
                        busy -> uiText("Создаём…", "Creating…")
                        createdSessionId != null -> uiText("Продолжить", "Continue")
                        else -> uiText("Создать", "Create")
                    }
                )
            }
        },
        dismissButton = {
            TextButton(enabled = !busy, onClick = onDismiss) { Text(uiText("Отмена", "Cancel")) }
        },
    )
}
