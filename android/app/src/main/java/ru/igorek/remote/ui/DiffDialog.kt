package ru.igorek.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.DiffHunkDto
import ru.igorek.remote.data.DiffLineDto
import ru.igorek.remote.data.FileDiffResponse

internal fun diffLineTint(type: String): DiffTint = when (type.trim()) {
    "+" -> DiffTint.Positive
    "-" -> DiffTint.Negative
    else -> DiffTint.Neutral
}

internal fun fileDiffUnavailable(diff: FileDiffResponse?): Boolean {
    if (diff == null) return false
    if (diff.source.trim().lowercase() == "unavailable") return true
    return diff.hunks.all { it.lines.isEmpty() }
}

internal fun fileDiffNotice(diff: FileDiffResponse, language: AppLanguage = AppLocale.current): String? {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when {
        fileDiffUnavailable(diff) -> pick("diff недоступен", "diff unavailable")
        diff.truncated -> pick("diff обрезан — показаны не все строки", "diff truncated — not all lines shown")
        else -> null
    }
}

internal fun diffSourceLabel(source: String, language: AppLanguage = AppLocale.current): String {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when (source.trim().lowercase()) {
        "checkpoint" -> pick("чекпоинт", "checkpoint")
        "unavailable" -> pick("недоступен", "unavailable")
        else -> source
    }
}

/** Full diff text as-is: including header labels and lines with prefixes, without truncation. */
internal fun diffCopyText(diff: FileDiffResponse): String =
    diff.hunks.joinToString("\n") { hunk ->
        buildList {
            if (hunk.header.isNotBlank()) add(hunk.header)
            hunk.lines.forEach { add(it.type + it.text) }
        }.joinToString("\n")
    }

@Composable
internal fun DiffDialog(
    path: String,
    diff: FileDiffResponse?,
    onDismiss: () -> Unit,
    subtitle: String? = null,
) {
    val language = LocalAppLanguage.current
    val visible = diff?.takeIf { it.path == path || it.path.isBlank() }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        path,
                        modifier = Modifier.weight(1f),
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.titleSmall,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (visible != null) CopyButton(diffCopyText(visible))
                }
                subtitle?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                if (visible != null) DiffHeaderStats(visible, language)
            }
        },
        text = {
            if (visible == null) {
                Text(
                    uiText("Загружаем diff…", "Loading diff…"),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                DiffBody(visible, language)
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(uiText("Закрыть", "Close")) }
        },
    )
}

@Composable
private fun DiffHeaderStats(diff: FileDiffResponse, language: AppLanguage) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            additionStat(diff.additions).text,
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = diffStatColor(DiffTint.Positive),
        )
        Text(
            deletionStat(diff.deletions).text,
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = diffStatColor(DiffTint.Negative),
        )
        Text(
            diffSourceLabel(diff.source, language),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun DiffBody(diff: FileDiffResponse, language: AppLanguage) {
    val notice = fileDiffNotice(diff, language)
    if (fileDiffUnavailable(diff)) {
        Text(
            notice.orEmpty(),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (notice != null) {
            Text(
                notice,
                style = MaterialTheme.typography.labelSmall,
                color = Warning,
            )
        }
        LazyColumn(Modifier.heightIn(max = 420.dp)) {
            items(diff.hunks) { hunk -> DiffHunk(hunk) }
        }
    }
}

@Composable
internal fun DiffHunk(hunk: DiffHunkDto) {
    val scroll = rememberScrollState()
    Column(Modifier.fillMaxWidth().padding(bottom = 6.dp)) {
        if (hunk.header.isNotBlank()) {
            Text(
                hunk.header,
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Column(Modifier.fillMaxWidth().horizontalScroll(scroll)) {
            hunk.lines.forEach { line -> DiffLineRow(line) }
        }
    }
}

@Composable
private fun DiffLineRow(line: DiffLineDto) {
    val tint = diffLineTint(line.type)
    val background = when (tint) {
        DiffTint.Positive -> Success.copy(alpha = 0.12f)
        DiffTint.Negative -> Danger.copy(alpha = 0.12f)
        DiffTint.Neutral -> Color.Transparent
    }
    val color = when (tint) {
        DiffTint.Positive -> Success
        DiffTint.Negative -> Danger
        DiffTint.Neutral -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Row(
        Modifier
            .fillMaxWidth()
            .background(background)
            .padding(horizontal = 5.dp, vertical = 1.dp),
    ) {
        Text(
            text = line.type + line.text,
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall,
            color = color,
            softWrap = false,
        )
    }
}
