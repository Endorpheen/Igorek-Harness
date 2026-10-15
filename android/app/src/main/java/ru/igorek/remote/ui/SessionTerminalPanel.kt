package ru.igorek.remote.ui

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.coroutines.launch
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.TerminalFrames

private class TerminalSurface(private val view: WebView, private val status: (String, Boolean) -> Unit) {
    private var socket: WebSocket? = null
    private val frames = TerminalFrames { socket?.send(it) ?: false }
    @Volatile private var disposed = false

    @Synchronized fun send(kind: String, payload: JSONObject) {
        frames.input(kind, Json.parseToJsonElement(payload.toString()).jsonObject)
    }
    fun connect(ticket: String, rows: Int, cols: Int) {
        if (disposed) return
        socket = Bridge.terminalSocket(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (disposed) { webSocket.cancel(); return }
                synchronized(this@TerminalSurface) {
                    socket = webSocket
                    frames.open(ticket, rows, cols)
                }
                show("Запуск оболочки в VM…")
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                val kind = JSONObject(text).optString("kind")
                frames.received(kind)
                if (kind == "opened") show("Оболочка готова", true)
                view.post { if (!disposed) view.evaluateJavascript("receive(${JSONObject.quote(text)})", null) }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                frames.received("disconnected")
                show("Нет соединения: ${t.message.orEmpty()}")
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (frames.ready) { frames.received("disconnected"); show("Отключён") }
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, reason) }
        })
    }
    fun show(text: String, ready: Boolean = false) { view.post { if (!disposed) status(text, ready) } }
    fun input(text: String): Boolean = frames.text(text)
    fun dispose() { disposed = true; socket?.close(1000, "detach"); socket = null; view.removeJavascriptInterface("Native"); view.destroy() }
}

private fun Context.terminalActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.terminalActivity()
    else -> null
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun SessionTerminalPanel(sessionId: String, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    var generation by remember(sessionId) { mutableIntStateOf(0) }
    var status by remember(sessionId, generation) { mutableStateOf("Подключение…") }
    var terminal by remember(sessionId, generation) { mutableStateOf<TerminalSurface?>(null) }
    var ready by remember(sessionId, generation) { mutableStateOf(false) }
    var inputView by remember(sessionId, generation) { mutableStateOf<TerminalKeyboardView?>(null) }
    val window = LocalContext.current.terminalActivity()?.window
    DisposableEffect(window) {
        val previous = window?.attributes?.softInputMode
        window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        onDispose { if (previous != null) window.setSoftInputMode(previous) }
    }
    val scope = rememberCoroutineScope()
    val inputColor = MaterialTheme.colorScheme.onSurfaceVariant.toArgb()
    DisposableEffect(sessionId, generation) { onDispose { terminal?.dispose() } }
    Column(modifier.imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Терминал KVM", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
            IconButton(onClick = { generation++ }) { Icon(Icons.Default.Refresh, "Переподключиться") }
            IconButton(onClick = onDismiss) { Icon(Icons.Default.Close, "Закрыть") }
        }
        Text(status, Modifier.padding(horizontal = 12.dp), style = MaterialTheme.typography.labelSmall)
        if (sessionId.startsWith("agent-")) {
            Text("Терминал доступен в основной сессии. Вернись к Main во вкладке «Агенты».", Modifier.padding(12.dp))
        } else key(sessionId, generation) {
            AndroidView(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                factory = { context ->
                    WebView(context).apply {
                        isFocusable = false
                        isFocusableInTouchMode = false
                        layoutParams = android.view.ViewGroup.LayoutParams(android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.MATCH_PARENT)
                        settings.javaScriptEnabled = true
                        settings.allowFileAccess = false
                        settings.allowContentAccess = false
                        settings.blockNetworkLoads = true
                        webViewClient = object : WebViewClient() {
                            override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest) = true
                        }
                        val surface = TerminalSurface(this) { text, active -> status = text; ready = active }
                        terminal = surface
                        addJavascriptInterface(object {
                            @JavascriptInterface fun ready(rows: Int, cols: Int) {
                                post { scope.launch {
                                    try { surface.connect(Bridge.terminalTicket(sessionId), rows, cols) }
                                    catch (e: kotlinx.coroutines.CancellationException) { throw e }
                                    catch (e: Exception) { surface.show("Не удалось подключиться: ${e.message}") }
                                } }
                            }
                            @JavascriptInterface fun send(raw: String) {
                                val message = JSONObject(raw)
                                surface.send(message.getString("kind"), message.getJSONObject("payload"))
                            }
                            @JavascriptInterface fun status(kind: String, text: String) {
                                surface.show(if (kind == "exit") "Оболочка завершена: $text" else "Ошибка: $text")
                            }
                        }, "Native")
                        loadUrl("file:///android_asset/terminal/index.html")
                    }
                },
            )
        }
        if (!sessionId.startsWith("agent-")) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                key(sessionId, generation) {
                    AndroidView(
                        modifier = Modifier.weight(1f).height(56.dp),
                        factory = { context -> TerminalKeyboardView(context) { terminal?.input(it) ?: false }.also { inputView = it } },
                        update = { it.isEnabled = ready; it.setHintTextColor(inputColor) },
                    )
                }
                IconButton(onClick = { inputView?.keyboard?.key("\r") }, enabled = ready) {
                    Icon(Icons.Default.Send, "Enter — выполнить строку")
                }
            }
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            IconButton(onClick = {
                inputView?.let { view ->
                    view.requestFocus()
                    view.post {
                        (view.context.getSystemService(Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                            .showSoftInput(view, android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT)
                    }
                }
            }, enabled = ready) {
                Icon(Icons.Default.Keyboard, "Показать клавиатуру")
            }
            listOf("Ctrl+C" to "\u0003", "Tab" to "\t", "Esc" to "\u001b", "↑" to "\u001b[A", "↓" to "\u001b[B", "←" to "\u001b[D", "→" to "\u001b[C", "Enter" to "\r").forEach { (label, value) ->
                TextButton(onClick = { inputView?.keyboard?.key(value) }, enabled = ready) { Text(label) }
            }
        }
    }
}
