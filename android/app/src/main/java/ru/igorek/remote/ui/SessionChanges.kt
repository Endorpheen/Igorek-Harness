package ru.igorek.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.ChangedFileDto
import ru.igorek.remote.data.ChangedFilesResponse

internal enum class DiffTint { Positive, Negative, Neutral }

internal data class DiffStat(val text: String, val tint: DiffTint)

internal fun changedFileCountLabel(count: Int, language: AppLanguage = AppLocale.current): String {
    if (language == AppLanguage.English) return "$count ${if (count == 1) "file" else "files"}"
    val modulo100 = count % 100
    val modulo10 = count % 10
    val noun = when {
        modulo100 in 11..14 -> "файлов"
        modulo10 == 1 -> "файл"
        modulo10 in 2..4 -> "файла"
        else -> "файлов"
    }
    return "$count $noun"
}

internal fun additionStat(additions: Int): DiffStat = DiffStat("+$additions", DiffTint.Positive)

internal fun deletionStat(deletions: Int): DiffStat = DiffStat("−$deletions", DiffTint.Negative)

internal fun diffStatColor(tint: DiffTint): Color = when (tint) {
    DiffTint.Positive -> Success
    DiffTint.Negative -> Danger
    DiffTint.Neutral -> Info
}

internal fun changeStatusLabel(status: String, language: AppLanguage = AppLocale.current): String {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when (status.trim().lowercase()) {
        "added" -> pick("добавлен", "added")
        "deleted" -> pick("удалён", "deleted")
        "modified" -> pick("изменён", "modified")
        else -> status
    }
}

internal fun changeStatusTint(status: String): DiffTint = when (status.trim().lowercase()) {
    "added" -> DiffTint.Positive
    "deleted" -> DiffTint.Negative
    else -> DiffTint.Neutral
}

internal fun changeStatusIcon(status: String): ImageVector = when (status.trim().lowercase()) {
    "added" -> Icons.Default.Add
    "deleted" -> Icons.Default.Delete
    else -> Icons.Default.Edit
}

internal fun sessionChangesVisible(changes: ChangedFilesResponse?): Boolean =
    changes != null &&
        (changes.files.isNotEmpty() || changes.totals.additions > 0 || changes.totals.deletions > 0)

internal fun changedFilesCount(changes: ChangedFilesResponse): Int =
    maxOf(changes.totals.files, changes.files.size)

internal fun changedFilesSummary(changes: ChangedFilesResponse, language: AppLanguage = AppLocale.current): String =
    "${changedFileCountLabel(changedFilesCount(changes), language)}  " +
        "${additionStat(changes.totals.additions).text} ${deletionStat(changes.totals.deletions).text}"

// Chip tag in the top bar: only +N and −M, without text indicating the number of files,
// otherwise, on a narrow screen, the line would be truncated and look ugly.
internal fun changedChipStats(changes: ChangedFilesResponse): List<DiffStat> = listOf(
    additionStat(changes.totals.additions),
    deletionStat(changes.totals.deletions),
)

// Tap on a file line: send the bridge a pair (session, path) and return the path,
// which the dialog uses as the selected one. A pure function — verified by a test.
internal fun requestDiffFor(
    file: ChangedFileDto,
    sessionId: String,
    load: (String, String) -> Unit,
): String {
    load(sessionId, file.path)
    return file.path
}

@Composable
internal fun SessionChangesChip(changes: ChangedFilesResponse, onClick: () -> Unit) {
    if (!sessionChangesVisible(changes)) return
    val language = LocalAppLanguage.current
    AssistChip(
        onClick = onClick,
        modifier = Modifier
            .semantics { contentDescription = changedFilesSummary(changes, language) },
        label = {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                changedChipStats(changes).forEach { DiffStatText(it) }
            }
        },
        leadingIcon = {
            Icon(Icons.Default.Description, contentDescription = null, modifier = Modifier.size(14.dp))
        },
    )
}

@Composable
internal fun SessionChangesDialog(
    changes: ChangedFilesResponse,
    onFileClick: (ChangedFileDto) -> Unit,
    onDismiss: () -> Unit,
) {
    val language = LocalAppLanguage.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(uiText("Изменённые файлы", "Changed files")) },
        text = { ChangedFilesSummaryList(changes, language, onFileClick) },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(uiText("Закрыть", "Close")) }
        },
    )
}

// General dialog body for modified file dialogs: a summary chip and a string list.
// Used in both session dialog and version dialog—behavior is identical.
@Composable
internal fun ChangedFilesSummaryList(
    changes: ChangedFilesResponse,
    language: AppLanguage,
    onFileClick: (ChangedFileDto) -> Unit,
) {
    val count = changedFilesCount(changes)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(changedFileCountLabel(count, language), style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.weight(1f))
            DiffStatText(additionStat(changes.totals.additions))
            DiffStatText(deletionStat(changes.totals.deletions))
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
        if (changes.files.isEmpty()) {
            Text(
                uiText("Файлы пока не менялись", "No files changed yet"),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            LazyColumn(
                Modifier.heightIn(max = 420.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(changes.files, key = { it.path }) { file ->
                    ChangedFileRow(file, language, onFileClick)
                }
            }
        }
    }
}

@Composable
private fun DiffStatText(stat: DiffStat) {
    Text(
        stat.text,
        style = MaterialTheme.typography.labelSmall,
        fontFamily = FontFamily.Monospace,
        color = diffStatColor(stat.tint),
    )
}

@Composable
private fun ChangedFileRow(
    file: ChangedFileDto,
    language: AppLanguage,
    onClick: (ChangedFileDto) -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable { onClick(file) }
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(
            changeStatusIcon(file.status),
            contentDescription = changeStatusLabel(file.status, language),
            tint = diffStatColor(changeStatusTint(file.status)),
            modifier = Modifier.size(18.dp),
        )
        Column(Modifier.weight(1f)) {
            Text(
                file.path,
                fontFamily = FontFamily.Monospace,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                changeStatusLabel(file.status, language),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        DiffStatText(additionStat(file.additions))
        DiffStatText(deletionStat(file.deletions))
    }
}
