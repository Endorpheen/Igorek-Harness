package ru.igorek.remote.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeException
import ru.igorek.remote.data.BrowserPreviewResponse
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private fun decodeBrowserFrame(frame: BrowserPreviewResponse): Bitmap? {
    if (frame.imageBase64.length !in 1..426668 || frame.width !in 1..960 || frame.height !in 1..960) return null
    val bytes = Base64.decode(frame.imageBase64, Base64.NO_WRAP)
    if (bytes.size > 320000) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth != frame.width || bounds.outHeight != frame.height || bounds.outMimeType != "image/jpeg") return null
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
}

@Composable
private fun browserPreviewReason(reason: String): String = when (reason) {
    "not_desktop" -> uiText("Просмотр доступен при подключении к десктопному Игорьку.", "Connect to desktop Igorek to view its browser.")
    "desktop_unavailable", "desktop_locked" -> uiText("Разблокируй хранилище десктопа и запусти нужный профиль.", "Unlock the desktop vault and start your profile.")
    "manual_control" -> uiText("Сейчас браузером управляешь ты. Передай его агенту на компьютере, чтобы включить просмотр.", "The browser is under manual control. Hand it to the agent on your computer to enable the preview.")
    "browser_paused" -> uiText("Браузер на паузе. Проверь его состояние на компьютере.", "The browser is paused. Check it on your computer.")
    "browser_closed", "browser_hidden" -> uiText("Открой браузер в десктопном Игорьке.", "Open the browser in desktop Igorek.")
    "busy", "browser_changed" -> uiText("Вкладка обновляется…", "The tab is updating…")
    else -> uiText("Изображение пока недоступно. Попробуй обновить.", "The preview is unavailable. Try refreshing.")
}

@Composable
internal fun SessionBrowserPanel(onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val server by Bridge.serverUrl.collectAsState()
    val device by Bridge.currentDeviceId.collectAsState()
    var refresh by remember { mutableIntStateOf(0) }
    var frame by remember(server, device) { mutableStateOf<BrowserPreviewResponse?>(null) }
    var bitmap by remember(server, device) { mutableStateOf<Bitmap?>(null) }
    var error by remember(server, device) { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var selecting by remember(server, device) { mutableStateOf(false) }
    var tabsExpanded by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    var large by remember { mutableStateOf(false) }
    val networkMessage = uiText("Не удалось обновить кадр. Проверь соединение с десктопом.", "Could not refresh the frame. Check the desktop connection.")
    val switchingBusyMessage = uiText("Кадр обновляется. Нажми на вкладку ещё раз.", "The frame is updating. Try selecting the tab again.")
    val switchingErrorMessage = uiText("Не удалось переключить вкладку. Обнови список и попробуй снова.", "Could not switch tabs. Refresh the list and try again.")
    val updateMessage = uiText("Для просмотра браузера обнови десктопное приложение.", "Update the desktop app to enable browser previews.")
    LaunchedEffect(server, device, refresh, lifecycle) {
        large = false
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            bitmap = null; frame = null; large = false
            while (isActive) {
                var retry = true
                busy = true
                try {
                    val next = Bridge.browserPreview(if (bitmap != null) frame?.frameId.orEmpty() else "")
                    if (!next.ready) {
                        bitmap = null; large = false
                    } else if (!next.unchanged) {
                        val decoded = withContext(Dispatchers.Default) { decodeBrowserFrame(next) }
                        if (decoded == null) throw IllegalStateException("Invalid browser image")
                        bitmap = decoded
                    }
                    frame = next; error = null
                    retry = next.reason != "not_desktop"
                } catch (cancel: CancellationException) {
                    throw cancel
                } catch (failure: Exception) {
                    error = if (failure is BridgeException && failure.status == 404) updateMessage else networkMessage
                    bitmap = null; frame = null; large = false
                    if (failure is BridgeException && failure.status in listOf(401, 403, 404)) retry = false
                } finally { busy = false }
                if (!retry) break
                delay(2000)
            }
        }
    }
    Column(modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Language, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(8.dp))
            Text(uiText("Браузер", "Browser"), Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
            IconButton(onClick = { refresh++ }, enabled = !busy && !selecting) { Icon(Icons.Default.Refresh, uiText("Обновить кадр", "Refresh frame")) }
            IconButton(onClick = onDismiss) { Icon(Icons.Default.Close, uiText("Закрыть", "Close")) }
        }
        HorizontalDivider()
        Column(Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(uiText("Просмотр десктопа · выбор вкладок", "Desktop preview · tab selection"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            val tabs = frame?.tabs.orEmpty()
            if (tabs.isNotEmpty()) Box(Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { tabsExpanded = true }, enabled = !selecting, modifier = Modifier.fillMaxWidth()) {
                    Text(if (selecting) uiText("Переключаю…", "Switching…") else tabs.firstOrNull { it.id == frame?.activeTabId }?.title?.takeIf { it.isNotBlank() } ?: uiText("Выбрать вкладку", "Select a tab"), Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Icon(Icons.Default.ArrowDropDown, null)
                }
                DropdownMenu(expanded = tabsExpanded, onDismissRequest = { tabsExpanded = false }, modifier = Modifier.heightIn(max = 320.dp)) {
                    tabs.forEach { tab ->
                        DropdownMenuItem(text = { Text(tab.title.ifBlank { tab.url.ifBlank { uiText("Вкладка", "Tab") } }, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                            trailingIcon = { if (tab.id == frame?.activeTabId) Icon(Icons.Default.Check, null) },
                            onClick = {
                                tabsExpanded = false
                                if (!selecting) scope.launch {
                                    selecting = true
                                    try {
                                        val result = Bridge.selectBrowserTab(tab.id)
                                        if (result.selected) { bitmap = null; frame = null; error = null; refresh++ }
                                        else if (result.reason == "busy") { error = switchingBusyMessage }
                                        else { error = switchingErrorMessage; refresh++ }
                                    } catch (cancel: CancellationException) { throw cancel }
                                    catch (failure: Exception) { error = networkMessage }
                                    finally { selecting = false }
                                }
                            })
                    }
                }
            }
            frame?.title?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis) }
            frame?.url?.takeIf { it.isNotBlank() }?.let { Text(it, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis) }
            if (busy && bitmap == null) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            val image = bitmap
            if (image != null) {
                Text(if (error != null) uiText("Последний полученный кадр", "Last received frame") else if (frame?.loading == true) uiText("Страница загружается…", "Page is loading…") else uiText("Открытая вкладка десктопа", "Desktop browser tab"), style = MaterialTheme.typography.labelSmall)
                Image(image.asImageBitmap(), contentDescription = uiText("Изображение браузера. Нажми, чтобы увеличить", "Browser preview. Tap to enlarge"), modifier = Modifier.fillMaxWidth().heightIn(min = 140.dp, max = 380.dp).clickable { large = true }, contentScale = ContentScale.Fit)
                Text(uiText("Нажми на кадр для увеличения", "Tap the frame to enlarge"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                val time = frame?.capturedAt?.takeIf { it > 0 }?.let { SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(it)) }.orEmpty()
                Text(uiText("Кадр $time · обновление каждые 2 с", "Frame $time · refreshes every 2 s"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else if (!busy && error == null) {
                Text(browserPreviewReason(frame?.reason.orEmpty()), style = MaterialTheme.typography.bodySmall)
            }
        }
    }
    if (large && bitmap != null) BrowserFrameDialog(bitmap!!, error != null) { large = false }
}

@Composable
private fun BrowserFrameDialog(bitmap: Bitmap, stale: Boolean, onDismiss: () -> Unit) {
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    BoxWithConstraints {
        Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
            BoxWithConstraints(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface).systemBarsPadding()) {
                val width = constraints.maxWidth.toFloat(); val height = constraints.maxHeight.toFloat()
                val transform = rememberTransformableState { zoom, pan, _ ->
                    scale = (scale * zoom).coerceIn(1f, 4f)
                    val limitX = width * (scale - 1) / 2; val limitY = height * (scale - 1) / 2
                    offset = Offset((offset.x + pan.x).coerceIn(-limitX, limitX), (offset.y + pan.y).coerceIn(-limitY, limitY))
                }
                Box(Modifier.fillMaxSize().clipToBounds().transformable(transform)) {
                    Image(bitmap.asImageBitmap(), null, Modifier.fillMaxSize().graphicsLayer { scaleX = scale; scaleY = scale; translationX = offset.x; translationY = offset.y }, contentScale = ContentScale.Fit)
                }
                Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface.copy(alpha = .9f)).padding(start = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(if (stale) uiText("Последний кадр", "Last frame") else uiText("Браузер · просмотр", "Browser · preview"), Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                    IconButton(onClick = onDismiss) { Icon(Icons.Default.Close, uiText("Закрыть изображение", "Close image")) }
                }
            }
        }
    }
}
