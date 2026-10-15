package ru.igorek.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import ru.igorek.remote.data.*

private fun changeCounts(additions: Int?, deletions: Int?) = buildAnnotatedString {
    withStyle(SpanStyle(color = Success)) { append(additions?.let { "+$it" } ?: "?") }
    append(" ")
    withStyle(SpanStyle(color = Danger)) { append(deletions?.let { "−$it" } ?: "?") }
}

@Composable
internal fun SessionChangesReviewPanel(sessionId: String, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    val recordedChanges by Bridge.sessionChanges.collectAsState()
    var mode by remember(sessionId) { mutableStateOf("session") }
    var base by remember(sessionId) { mutableStateOf("HEAD") }
    var untracked by remember(sessionId) { mutableStateOf(false) }
    var refresh by remember(sessionId) { mutableIntStateOf(0) }
    var data by remember(sessionId) { mutableStateOf<ChangesReview?>(null) }
    var loading by remember(sessionId) { mutableStateOf(false) }
    var error by remember(sessionId) { mutableStateOf<String?>(null) }
    var query by remember(sessionId) { mutableStateOf("") }
    var filter by remember(sessionId) { mutableStateOf("") }
    var selected by remember(sessionId, mode, base, untracked) { mutableStateOf<String?>(null) }
    var file by remember(sessionId, mode, base, untracked) { mutableStateOf<ReviewFile?>(null) }
    var fileError by remember(sessionId, selected, refresh) { mutableStateOf<String?>(null) }
    var fileLoading by remember(sessionId, selected) { mutableStateOf(false) }
    var collapsed by remember(sessionId) { mutableStateOf(setOf<String>()) }

    LaunchedEffect(sessionId, mode, base, untracked, refresh, recordedChanges) {
        loading = true; error = null; data = null; file = null
        try { data = Bridge.changesReview(sessionId, mode, base, untracked) }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "Comparison unavailable" }
        finally { loading = false }
    }
    LaunchedEffect(sessionId, selected, data) {
        file = null; fileError = null
        val path = selected ?: return@LaunchedEffect
        val review = data ?: return@LaunchedEffect
        if (review.files.none { it.path == path }) { selected = null; return@LaunchedEffect }
        fileLoading = true
        try { file = Bridge.changesReviewFile(sessionId, mode, base, untracked, path, review.revision) }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { fileError = e.message ?: "Diff unavailable" }
        finally { fileLoading = false }
    }
    val visible = reviewFiles(data?.files.orEmpty(), query, filter)
    val index = visible.indexOfFirst { it.path == selected }
    Column(modifier.padding(horizontal = 12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(uiText("Изменения", "Changes"), Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
            IconButton(onClick = { refresh++ }, enabled = !loading) { Icon(Icons.Default.Refresh, uiText("Обновить", "Refresh")) }
            IconButton(onClick = onDismiss) { Icon(Icons.Default.Close, uiText("Закрыть", "Close")) }
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("session" to uiText("Сессия", "Session"), "working" to uiText("Незакоммиченные", "Uncommitted"), "branch" to uiText("Ветка", "Branch")).forEach { (value, label) ->
                FilterChip(selected = mode == value, onClick = { mode = value }, label = { Text(label) })
            }
        }
        if (mode == "branch") {
            var expanded by remember { mutableStateOf(false) }
            Box {
                TextButton(onClick = { expanded = true }) { Text(uiText("База: ", "Base: ") + base) }
                DropdownMenu(expanded, onDismissRequest = { expanded = false }) {
                    (listOf("HEAD") + data?.refs.orEmpty()).distinct().forEach { ref ->
                        DropdownMenuItem(text = { Text(ref) }, onClick = { base = ref; expanded = false })
                    }
                }
            }
        }
        if (mode != "session") Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = untracked, onCheckedChange = { untracked = it })
            Text(uiText("Новые файлы вне Git", "Untracked files"), style = MaterialTheme.typography.bodySmall)
        }
        val review = data
        if (review != null) {
            Text(
                buildAnnotatedString {
                    append(if (review.totals.partial) uiText("Частичный итог: ", "Partial total: ") else uiText("Файлов: ", "Files: "))
                    append("${review.totals.files}  ")
                    append(changeCounts(review.totals.additions, review.totals.deletions))
                },
                style = MaterialTheme.typography.labelMedium,
            )
            Text(
                when (mode) {
                    "working" -> if (review.revision.isEmpty()) review.baseLabel else "HEAD (${review.revision.take(8)}) → " + uiText("рабочая папка", "working tree")
                    "branch" -> uiText("Общий предок с ", "Common ancestor with ") + base + " (${review.revision.take(8)}) → " + uiText("рабочая папка", "working tree")
                    else -> review.baseLabel
                }, style = MaterialTheme.typography.labelSmall,
            )
            if (review.warning.isNotBlank()) Text(review.warning, color = Warning, style = MaterialTheme.typography.labelSmall)
            if (review.totals.unknown > 0) Text(uiText("Без счётчика строк: ", "Line count unavailable: ") + review.totals.unknown, color = Warning, style = MaterialTheme.typography.labelSmall)
            if (review.omitted > 0) Text(uiText("Не показано из-за лимитов: ", "Omitted due to limits: ") + review.omitted, color = Warning, style = MaterialTheme.typography.labelSmall)
            if (review.excludedUntracked > 0) Text(uiText("Новых файлов вне Git исключено: ", "Untracked files excluded: ") + review.excludedUntracked, style = MaterialTheme.typography.labelSmall)
        }
        error?.let { Text(it, color = Danger, style = MaterialTheme.typography.bodySmall) }
        if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (selected != null) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { selected = null }) { Text(uiText("К файлам", "Files")) }
                TextButton(onClick = { selected = visible[index - 1].path }, enabled = index > 0) { Text("←") }
                Text(if (index >= 0) "${index + 1}/${visible.size}" else "", style = MaterialTheme.typography.labelSmall)
                TextButton(onClick = { selected = visible[index + 1].path }, enabled = index >= 0 && index < visible.lastIndex) { Text("→") }
            }
            Text(selected.orEmpty(), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
            fileError?.let { Text(it, color = Danger) }
            if (fileLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
            file?.let { value ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(changeCounts(value.additions, value.deletions), Modifier.weight(1f))
                    CopyButton(diffCopyText(value.diff()))
                }
                if (value.oldPath.isNotBlank()) Text(value.oldPath + " → " + value.path, style = MaterialTheme.typography.labelSmall)
                if (value.source == "unavailable") Text(value.note.ifBlank { uiText("Сравнение недоступно", "Comparison unavailable") }, color = Warning)
                else if (value.hunks.isEmpty()) Text(uiText("Построчных изменений нет", "No line changes"))
                if (value.truncated) Text(uiText("Показаны не все строки", "Not all lines shown"), color = Warning)
                LazyColumn(Modifier.weight(1f)) { items(value.hunks) { DiffHunk(it) } }
            }
        } else {
            OutlinedTextField(value = query, onValueChange = { query = it }, singleLine = true, modifier = Modifier.fillMaxWidth(), label = { Text(uiText("Поиск файлов", "Search files")) })
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                listOf("" to uiText("Все", "All"), "added" to uiText("Добавлены", "Added"), "modified" to uiText("Изменены", "Modified"), "deleted" to uiText("Удалены", "Deleted"), "renamed" to uiText("Переименованы", "Renamed")).forEach { (status, label) ->
                    FilterChip(selected = filter == status, onClick = { filter = status }, label = { Text(label) })
                }
            }
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!loading && error == null && visible.isEmpty()) item { Text(uiText("Подходящих изменений нет", "No matching changes")) }
                visible.groupBy { it.path.substringBeforeLast('/', "") }.forEach { (folder, files) ->
                    if (folder.isNotEmpty()) item(key = "folder:$folder") {
                        TextButton(onClick = { collapsed = if (folder in collapsed) collapsed - folder else collapsed + folder }) {
                            Text((if (folder in collapsed && query.isEmpty()) "▸ " else "▾ ") + folder)
                        }
                    }
                    if (folder.isEmpty() || folder !in collapsed || query.isNotEmpty()) items(files, key = { it.path }) { value ->
                        Column(Modifier.fillMaxWidth().clickable { selected = value.path }.padding(vertical = 8.dp)) {
                            Text(value.path.substringAfterLast('/'), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                            Text(buildAnnotatedString {
                                append(when (value.status) { "added" -> uiText("Добавлен", "Added"); "deleted" -> uiText("Удалён", "Deleted"); "renamed" -> uiText("Переименован", "Renamed"); else -> uiText("Изменён", "Modified") })
                                append("  ")
                                append(changeCounts(value.additions, value.deletions))
                            }, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }
}
