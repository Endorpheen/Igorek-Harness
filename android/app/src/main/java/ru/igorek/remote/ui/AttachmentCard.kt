package ru.igorek.remote.ui

import android.content.Intent
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.igorek.remote.data.AttachmentDto
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeClient
import java.io.File
import java.io.IOException

@Composable
fun AttachmentCard(sessionId: String, attachment: AttachmentDto) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val cannotOpenSave = uiText("Не удалось открыть место сохранения", "Could not open the save destination")
    val fileSaved = uiText("Файл сохранён", "File saved")
    val saveFailed = uiText("Не удалось сохранить файл", "Could not save the file")
    val downloadSaveFailed = uiText("Не удалось сохранить загрузку", "Could not save the download")
    val noViewer = uiText(
        "Нет приложения для этого типа файла. Можно сохранить файл.",
        "No app can open this file type. You can save the file instead.",
    )
    val downloadFailed = uiText("Загрузка не удалась. Повторите попытку.", "Download failed. Please try again.")
    var busy by remember(attachment.id) { mutableStateOf(false) }
    var error by remember(attachment.id) { mutableStateOf<String?>(null) }
    val received = remember(attachment.id) { MutableStateFlow(0L) }
    val bytes by received.collectAsState()
    val safeName = attachment.name.substringAfterLast('/').substringAfterLast('\\')
        .take(240).let { if (it.isBlank() || it == "." || it == "..") "file" else it }
    val cache = remember(attachment.id) {
        val key = BridgeClient.sha256("${Bridge.serverUrl.value}/$sessionId/${attachment.id}".toByteArray())
        File(context.cacheDir, "attachments/$key/$safeName")
    }
    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument(attachment.mimeType)) { uri ->
        if (uri != null) scope.launch {
            busy = true
            try {
                withContext(Dispatchers.IO) {
                    val output = context.contentResolver.openOutputStream(uri, "w")
                        ?: throw IOException(cannotOpenSave)
                    output.use { target -> cache.inputStream().use { it.copyTo(target) } }
                }
                Toast.makeText(context, fileSaved, Toast.LENGTH_SHORT).show()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                error = "$saveFailed: ${failure.message}"
            } finally { busy = false }
        }
    }

    fun fetch(open: Boolean) {
        busy = true
        error = null
        received.value = 0
        scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    if (!cache.exists() || cache.length() != attachment.size) {
                        cache.parentFile?.mkdirs()
                        val partial = File.createTempFile("download-", ".part", cache.parentFile)
                        try {
                            partial.outputStream().use { Bridge.download(sessionId, attachment, it) { count -> received.value = count } }
                            if (!partial.renameTo(cache)) throw IOException(downloadSaveFailed)
                        } finally { partial.delete() }
                    }
                }
                if (open) {
                    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", cache)
                    context.startActivity(Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(uri, attachment.mimeType)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    })
                } else save.launch(safeName)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: android.content.ActivityNotFoundException) {
                error = noViewer
            } catch (failure: Exception) {
                error = failure.message ?: downloadFailed
            } finally { busy = false }
        }
    }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(attachment.name, style = MaterialTheme.typography.titleSmall)
            Text("${attachment.size / 1024} ${uiText("КБ", "KB")} · ${attachment.mimeType}", style = MaterialTheme.typography.bodySmall)
            if (busy) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
                Text(
                    "${uiText("Загрузка", "Downloading")}: ${bytes / 1024} / ${attachment.size / 1024} ${uiText("КБ", "KB")}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(
                    onClick = { fetch(true) },
                    modifier = Modifier.weight(1f),
                    enabled = !busy,
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 8.dp),
                ) { Text(uiText("Открыть", "Open"), maxLines = 1, softWrap = false) }
                OutlinedButton(
                    onClick = { fetch(false) },
                    modifier = Modifier.weight(1f),
                    enabled = !busy,
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 8.dp),
                ) { Text(uiText("Сохранить", "Save"), maxLines = 1, softWrap = false) }
            }
        }
    }
}
