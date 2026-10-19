package ru.igorek.remote.ui

import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeClient
import ru.igorek.remote.data.UploadAttachmentDto
import java.io.File

internal fun uploadPreviewCache(context: Context, server: String, session: String, upload: String): File {
    val key = BridgeClient.sha256("$server/$session/$upload".toByteArray())
    return File(context.cacheDir, "message-previews/$key.image")
}

internal fun storeUploadPreview(context: Context, server: String, session: String, upload: String, bytes: ByteArray) {
    if (bytes.isEmpty() || bytes.size > 8388608) return
    val file = uploadPreviewCache(context, server, session, upload)
    file.parentFile?.mkdirs()
    val temporary = File.createTempFile("preview-", ".part", file.parentFile)
    try {
        temporary.writeBytes(bytes)
        temporary.renameTo(file)
    } finally { temporary.delete() }
}

@Composable
internal fun ImageThumbnail(bytes: ByteArray?, name: String, modifier: Modifier = Modifier) {
    val image by produceState<ImageBitmap?>(null, bytes) {
        value = null
        value = withContext(Dispatchers.IO) {
            if (bytes == null || bytes.isEmpty() || bytes.size > 8388608) return@withContext null
            runCatching {
                val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
                if (options.outWidth <= 0 || options.outHeight <= 0) return@runCatching null
                var sample = 1
                while (options.outWidth / sample > 256 || options.outHeight / sample > 256) sample *= 2
                options.inJustDecodeBounds = false
                options.inSampleSize = sample
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)?.asImageBitmap()
            }.getOrNull()
        }
    }
    Box(modifier.clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surface),
        contentAlignment = Alignment.Center) {
        image?.let {
            Image(it, contentDescription = uiText("Миниатюра: $name", "Thumbnail: $name"),
                modifier = Modifier.matchParentSize(), contentScale = ContentScale.Crop)
        } ?: Text(uiText("Изображение", "Image"), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(4.dp))
    }
}

@Composable
internal fun PendingImagePreview(bytes: ByteArray, name: String, enabled: Boolean, remove: () -> Unit) {
    Column(Modifier.width(108.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Box {
            ImageThumbnail(bytes, name, Modifier.size(96.dp))
            IconButton(onClick = remove, enabled = enabled, modifier = Modifier.align(Alignment.TopEnd).size(32.dp)
                .background(MaterialTheme.colorScheme.surface.copy(alpha = .9f), RoundedCornerShape(16.dp))) {
                Icon(Icons.Default.Close, contentDescription = uiText("Убрать $name", "Remove $name"), modifier = Modifier.size(18.dp))
            }
        }
        Text(name, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
internal fun MessageUploadAttachments(sessionId: String, attachments: List<UploadAttachmentDto>) {
    val context = LocalContext.current
    val server by Bridge.serverUrl.collectAsState()
    if (attachments.isEmpty()) return
    Column(Modifier.padding(top = 7.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            attachments.filter { it.isImage }.forEach { file ->
                val bytes by produceState<ByteArray?>(null, server, sessionId, file.uploadId, file.size) {
                    value = null
                    value = withContext(Dispatchers.IO) {
                        runCatching {
                            val cache = uploadPreviewCache(context, server, sessionId, file.uploadId)
                            if (file.size !in 1..8388608L) return@runCatching null
                            if (cache.isFile && cache.length() == file.size) cache.readBytes()
                            else Bridge.downloadUploadPreview(sessionId, file).also {
                                storeUploadPreview(context, server, sessionId, file.uploadId, it)
                            }
                        }.getOrNull()
                    }
                }
                Column(Modifier.width(112.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    ImageThumbnail(bytes, file.name, Modifier.size(104.dp))
                    Text(file.name, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        attachments.filterNot { it.isImage }.forEach {
            Text("📎 ${it.name}", style = MaterialTheme.typography.labelSmall)
        }
    }
}
