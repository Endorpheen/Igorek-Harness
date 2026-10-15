package ru.igorek.remote.ui

import android.view.MotionEvent
import android.view.ViewConfiguration
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.widget.FrameLayout
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloseFullscreen
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import ru.igorek.remote.R
import ru.igorek.remote.service.VoiceCalls
import kotlin.math.roundToInt

private class VoiceBubbleGesture {
    var pressed = false
    var talking = false
    var dragged = false
    var x = 0f
    var y = 0f
    var startOffset = Offset.Zero
    var job: Job? = null
    fun cancel() {
        job?.cancel(); job = null
        if (talking) VoiceCalls.cancelPushToTalk()
        pressed = false; talking = false
    }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
internal fun VoiceCallHost() {
    val call by VoiceCalls.state.collectAsState()
    if (call.sessionId.isEmpty()) return
    val context = LocalContext.current
    val minimized = !call.expanded
    var pulse by remember { mutableStateOf(false) }
    LaunchedEffect(call.speaking) {
        pulse = false
        while (call.speaking) { pulse = !pulse; delay(420) }
    }
    val scale by animateFloatAsState(if (pulse && call.speaking) 1.2f else 1f, tween(420), label = "voicePulse")
    val density = LocalDensity.current
    val configuration = LocalConfiguration.current
    val sizePx = with(density) { 64.dp.toPx() }
    val widthPx = with(density) { configuration.screenWidthDp.dp.toPx() }
    val heightPx = with(density) { configuration.screenHeightDp.dp.toPx() }
    var offset by remember { mutableStateOf(Offset((widthPx - sizePx - 16f).coerceAtLeast(0f), with(density) { 120.dp.toPx() })) }
    val gesture = remember { VoiceBubbleGesture() }
    val scope = rememberCoroutineScope()
    DisposableEffect(minimized, call.micMode) { onDispose { gesture.cancel() } }
    var info by remember { mutableStateOf(false) }
    var overlayAllowed by remember { mutableStateOf(Settings.canDrawOverlays(context)) }
    var overlayOffered by remember(call.sessionId) { mutableStateOf(false) }
    var offerOverlay by remember { mutableStateOf(false) }
    LaunchedEffect(call.connectedAt) { overlayAllowed = Settings.canDrawOverlays(context) }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                overlayAllowed = Settings.canDrawOverlays(context)
                VoiceCalls.service?.refreshOverlay()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    val overlaySettings = {
        context.startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${context.packageName}")))
    }
    val minimizeCall = {
        overlayAllowed = Settings.canDrawOverlays(context)
        if (!overlayAllowed && !overlayOffered && call.running) { overlayOffered = true; offerOverlay = true }
        else VoiceCalls.minimize()
    }
    val background = Color(0xFF090E18)
    val bubbleAccent = Color(0xFF8DCCF0)
    val octopusFilter = remember {
        ColorFilter.colorMatrix(ColorMatrix(floatArrayOf(
            0f, 0f, 0f, 0f, 141f,
            0f, 0f, 0f, 0f, 204f,
            0f, 0f, 0f, 0f, 240f,
            0f, 0f, 1.15f, 0f, -16f,
        )))
    }
    Popup(
        alignment = if (minimized) Alignment.TopStart else Alignment.Center,
        offset = if (minimized) IntOffset(offset.x.roundToInt(), offset.y.roundToInt()) else IntOffset.Zero,
        onDismissRequest = minimizeCall,
        properties = PopupProperties(focusable = !minimized, dismissOnClickOutside = false),
    ) {
        Surface(
            modifier = if (minimized) Modifier.size(64.dp) else Modifier.fillMaxSize(),
            shape = if (minimized) CircleShape else androidx.compose.ui.graphics.RectangleShape,
            color = background,
            shadowElevation = if (minimized) 8.dp else 0.dp,
        ) {
            Box {
                Column(Modifier.fillMaxSize().alpha(if (minimized) 0f else 1f)) {
                    Row(Modifier.fillMaxWidth().background(background).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(uiText("ГОЛОСОВОЙ РАЗГОВОР", "VOICE CALL"), modifier = Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, color = Color(0xFFAAB5C8))
                        Box {
                            IconButton(onClick = { info = true }) { Icon(Icons.Default.MoreVert, contentDescription = uiText("Меню разговора", "Call menu"), tint = Color(0xFFAAB5C8)) }
                            DropdownMenu(expanded = info, onDismissRequest = { info = false }) {
                                DropdownMenuItem(text = { Text(uiText("Рация · зажми и говори", "Push to talk · hold to speak")) }, leadingIcon = { if (call.micMode == "ptt") Icon(Icons.Default.Check, contentDescription = null) }, onClick = { info = false; VoiceCalls.setMicMode("ptt") })
                                DropdownMenuItem(text = { Text(uiText("Микрофон постоянно включён", "Always-on microphone")) }, leadingIcon = { if (call.micMode == "continuous") Icon(Icons.Default.Check, contentDescription = null) }, onClick = { info = false; VoiceCalls.setMicMode("continuous") })
                                HorizontalDivider()
                                Text(uiText("В режиме рации удерживай шарик, чтобы говорить. Короткое касание открывает звонок, перетаскивание перемещает шарик. Разговор продолжится в других приложениях.", "In push-to-talk mode, hold the bubble to speak. Tap to open the call, drag to move the bubble. The call continues in other apps."), Modifier.widthIn(max = 280.dp).padding(16.dp))
                                DropdownMenuItem(text = { Text(uiText(if (overlayAllowed) "Шарик поверх приложений: включён" else "Шарик поверх приложений: выключен", if (overlayAllowed) "Bubble over apps: enabled" else "Bubble over apps: disabled")) }, onClick = {
                                    info = false
                                    overlaySettings()
                                })
                            }
                        }
                        IconButton(onClick = minimizeCall) { Icon(Icons.Default.CloseFullscreen, contentDescription = uiText("Свернуть в шарик", "Minimize to bubble"), tint = Color.White) }
                    }
                    if (!overlayAllowed && call.running) TextButton(onClick = overlaySettings, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                        Text(uiText("Включить шарик поверх приложений", "Enable bubble over other apps"), color = bubbleAccent, style = MaterialTheme.typography.labelMedium)
                    }
                    if (call.error.isNotBlank() && !call.running) {
                        Column(Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                            Text("Игорёк", color = Color.White, style = MaterialTheme.typography.headlineLarge)
                            Spacer(Modifier.height(24.dp))
                            Text(call.error, color = Color(0xFFFFA3AF))
                            Spacer(Modifier.height(24.dp))
                            Button(onClick = { VoiceCalls.open(context, call.sessionId) }) { Text(uiText("Позвонить снова", "Call again")) }
                            TextButton(onClick = VoiceCalls::close) { Text(uiText("Закрыть", "Close")) }
                        }
                    } else {
                        AndroidView(
                            modifier = Modifier.weight(1f).fillMaxWidth(),
                            factory = { FrameLayout(it) },
                            onRelease = VoiceCalls::detach,
                            update = { if (call.ready) VoiceCalls.attach(it) },
                        )
                    }
                }
                if (minimized) {
                    FilledIconButton(
                        onClick = VoiceCalls::show,
                        modifier = Modifier.fillMaxSize().scale(if (call.speaking || call.pttRecording) scale else 1f).border(2.dp, bubbleAccent, CircleShape).pointerInteropFilter { event ->
                            when (event.actionMasked) {
                                MotionEvent.ACTION_DOWN -> {
                                    gesture.cancel(); gesture.pressed = true; gesture.dragged = false
                                    gesture.x = event.rawX; gesture.y = event.rawY; gesture.startOffset = offset
                                    if (call.micMode == "ptt") gesture.job = scope.launch {
                                        delay(200)
                                        if (gesture.pressed && !gesture.dragged && VoiceCalls.activityVisible && VoiceCalls.state.value.micMode == "ptt") {
                                            gesture.talking = true; VoiceCalls.beginPushToTalk()
                                        }
                                    }
                                }
                                MotionEvent.ACTION_MOVE -> {
                                    val dx = event.rawX - gesture.x; val dy = event.rawY - gesture.y
                                    val slop = ViewConfiguration.get(context).scaledTouchSlop
                                    if (!gesture.dragged && (kotlin.math.abs(dx) > slop || kotlin.math.abs(dy) > slop)) {
                                        gesture.dragged = true; gesture.job?.cancel()
                                        if (gesture.talking) VoiceCalls.cancelPushToTalk()
                                        gesture.talking = false
                                    }
                                    if (gesture.dragged) offset = Offset((gesture.startOffset.x + dx).coerceIn(0f, (widthPx - sizePx).coerceAtLeast(0f)), (gesture.startOffset.y + dy).coerceIn(0f, (heightPx - sizePx).coerceAtLeast(0f)))
                                }
                                MotionEvent.ACTION_UP -> {
                                    gesture.job?.cancel(); gesture.job = null
                                    val talked = gesture.talking; val active = gesture.pressed
                                    gesture.pressed = false; gesture.talking = false
                                    if (talked) VoiceCalls.endPushToTalk()
                                    else if (active && !gesture.dragged) VoiceCalls.show()
                                }
                                MotionEvent.ACTION_CANCEL -> gesture.cancel()
                            }
                            true
                        },
                        shape = CircleShape,
                        colors = IconButtonDefaults.filledIconButtonColors(containerColor = if (call.pttRecording) Color(0xFF214965) else Color(0xFF142B3D), contentColor = bubbleAccent),
                    ) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Image(painterResource(R.drawable.ic_igorek_octopus),
                                contentDescription = uiText(if (call.approval) "Нужно подтверждение. Открыть разговор" else if (call.micMode == "ptt") "Удерживай, чтобы говорить. Нажми, чтобы открыть разговор" else "Открыть разговор с Игорьком", if (call.approval) "Approval needed. Open call" else if (call.micMode == "ptt") "Hold to speak. Tap to open call" else "Open Igorek call"),
                                modifier = Modifier.fillMaxSize().padding(5.dp), colorFilter = octopusFilter)
                            val marker = when { call.pttRecording -> "●"; call.approval -> "!"; call.micMuted -> "∕"; else -> "" }
                            if (marker.isNotEmpty()) Box(Modifier.align(Alignment.BottomEnd).padding(4.dp).size(18.dp).background(bubbleAccent, CircleShape), contentAlignment = Alignment.Center) {
                                Text(marker, color = Color(0xFF142B3D), style = MaterialTheme.typography.labelSmall)
                            }
                        }
                    }
                }
            }
        }
    }
    if (offerOverlay) AlertDialog(
        onDismissRequest = { offerOverlay = false; VoiceCalls.minimize() },
        title = { Text(uiText("Шарик поверх приложений", "Bubble over other apps")) },
        text = { Text(uiText("Разреши Игорьку показывать окна поверх других приложений. Тогда во время сёрфинга останется шарик разговора. Без разрешения голос продолжит работать, а управление останется в уведомлении.", "Allow Igorek to display over other apps to keep the call bubble while browsing. Without permission, the call continues with notification controls.")) },
        confirmButton = { TextButton(onClick = { offerOverlay = false; overlaySettings() }) { Text(uiText("Разрешить", "Allow")) } },
        dismissButton = { TextButton(onClick = { offerOverlay = false; VoiceCalls.minimize() }) { Text(uiText("Пока уведомление", "Use notification")) } },
    )

}
