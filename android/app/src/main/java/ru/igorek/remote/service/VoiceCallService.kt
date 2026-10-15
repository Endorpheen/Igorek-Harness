package ru.igorek.remote.service

import android.Manifest
import android.annotation.SuppressLint
import android.animation.ValueAnimator
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.graphics.PixelFormat
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.*
import android.provider.Settings
import android.view.*
import android.webkit.*
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.json.JSONObject
import ru.igorek.remote.MainActivity
import ru.igorek.remote.R
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeException

class VoiceCallService : Service() {
    internal var webView: WebView? = null
        private set
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val handler = Handler(Looper.getMainLooper())
    private var sessionId = ""
    private var callId = ""
    @Volatile private var disposed = false
    private var tone: ToneGenerator? = null
    private var ringing = false
    private var holdTone: ToneGenerator? = null
    private var waitingTone = false
    private val hold = object : Runnable {
        override fun run() {
            if (!waitingTone || disposed) return
            holdTone?.startTone(ToneGenerator.TONE_PROP_PROMPT, 500)
            handler.postDelayed(this, 8000)
        }
    }
    private var audio: AudioManager? = null
    private var focus: AudioFocusRequest? = null
    private var oldAudioMode = AudioManager.MODE_NORMAL
    private var bubble: VoiceBubbleView? = null
    private var bubblePulse: ValueAnimator? = null
    private var bubbleX = 0
    private var bubbleY = 160
    private var bubbleHold: Runnable? = null
    private var bubbleTouchActive = false
    private var bubbleTalkActive = false
    private var bubbleWasRecording = false

    private fun cancelBubbleHold() {
        bubbleHold?.let(handler::removeCallbacks); bubbleHold = null
        if (bubbleTalkActive) cancelPushToTalk()
        bubbleTouchActive = false; bubbleTalkActive = false
    }
    private val ring = object : Runnable {
        override fun run() {
            if (!ringing || disposed) return
            tone?.startTone(ToneGenerator.TONE_SUP_RINGTONE, 1000)
            handler.postDelayed(this, 4000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Bridge.init(applicationContext)
        VoiceCalls.service = this
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, text("Разговор с Игорьком", "Igorek voice call"), NotificationManager.IMPORTANCE_LOW)
                .apply { setSound(null, null); description = text("Управление текущим голосовым разговором", "Controls for the ongoing voice call") },
        )
        try {
            if (Build.VERSION.SDK_INT >= 30) startForeground(NOTIFICATION, notification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
            else startForeground(NOTIFICATION, notification())
        } catch (_: RuntimeException) {
            finishCall(text("Android не разрешил фоновый микрофон. Начни разговор из открытого приложения.", "Android blocked background microphone access. Start a call with the app open."))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (disposed) return START_NOT_STICKY
        when (intent?.action) {
            START -> {
                val requested = intent.getStringExtra("session_id").orEmpty()
                if (requested.isBlank() || !Bridge.paired.value || ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    finishCall(text("Для разговора нужны подключение и разрешение на микрофон.", "A connection and microphone permission are required."))
                } else if (sessionId.isEmpty()) {
                    sessionId = requested
                    VoiceCalls.update { it.copy(sessionId = sessionId, running = true) }
                    if (acquireAudio()) {
                        createVoiceView()
                        scope.launch { Bridge.voiceEvents.collect { if (it.sessionId == sessionId) javascript("sessionEvent(${Json.encodeToString(it)})") } }
                        scope.launch { Bridge.paired.collect { if (!it) finishCall(text("Подключение к Игорьку отключено", "Igorek connection was removed")) } }
                        scope.launch { Bridge.screenshotsBlocked.collect { refreshOverlay() } }
                        scope.launch { VoiceCalls.state.collect { renderState(it) } }
                    }
                }
            }
            STOP -> finishCall()
            MIC -> VoiceCalls.command("voice-mic")
            else -> finishCall()
        }
        return START_NOT_STICKY
    }

    private fun acquireAudio(): Boolean {
        val manager = getSystemService(AudioManager::class.java)
        audio = manager
        oldAudioMode = manager.mode
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setOnAudioFocusChangeListener { change ->
                if (change == AudioManager.AUDIOFOCUS_LOSS || change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
                    finishCall(text("Разговор завершён: звук занят другим звонком или приложением.", "Call ended: another call or app took audio focus."))
                }
            }.build()
        focus = request
        if (manager.requestAudioFocus(request) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
            finishCall(text("Микрофон или звук занят. Попробуй после другого звонка.", "Audio is busy. Try again after the other call."))
            return false
        }
        manager.mode = AudioManager.MODE_IN_COMMUNICATION
        tone = runCatching { ToneGenerator(AudioManager.STREAM_VOICE_CALL, 35) }.getOrNull()
        holdTone = runCatching { ToneGenerator(AudioManager.STREAM_VOICE_CALL, 10) }.getOrNull()
        return true
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createVoiceView() {
        val view = WebView(this)
        webView = view
        view.setBackgroundColor(Color.rgb(9, 14, 24))
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            blockNetworkLoads = true
            mediaPlaybackRequiresUserGesture = false
        }
        view.addJavascriptInterface(VoiceNative(), "Native")
        view.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                handler.post {
                    if (!disposed && request.origin.toString().trimEnd('/') == ORIGIN &&
                        request.resources.all { it == PermissionRequest.RESOURCE_AUDIO_CAPTURE } &&
                        ContextCompat.checkSelfPermission(this@VoiceCallService, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                    ) request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) else request.deny()
                }
            }
        }
        view.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse {
                val name = request.url.lastPathSegment.orEmpty()
                if (request.url.scheme == "https" && request.url.host == "igorek-voice.local") {
                    if (request.url.path == "/igorek-octopus.jpg") {
                        return WebResourceResponse("image/jpeg", null, assets.open("voice/igorek-octopus.jpg"))
                    }
                    if (name in setOf("index.html", "voice.js", "i18n.js", "call-ui.js", "speech-policy.js", "render.js")) {
                        return WebResourceResponse(if (name.endsWith("html")) "text/html" else "text/javascript", "UTF-8", assets.open("voice/$name"))
                    }
                }
                return WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), java.io.ByteArrayInputStream(byteArrayOf()))
            }
            override fun onPageFinished(view: WebView, url: String) {
                if (disposed || url != "$ORIGIN/index.html") return
                VoiceCalls.update { it.copy(ready = true) }
                javascript("initialize(${JSONObject.quote(sessionId)})")
            }
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                finishCall(text("Голосовой экран остановлен Android. Начни разговор заново.", "Android stopped the voice renderer. Start a new call."))
                return true
            }
        }
        view.loadUrl("$ORIGIN/index.html")
    }

    private inner class VoiceNative {
        @JavascriptInterface fun speaking(active: Boolean) { handler.post { if (!disposed) VoiceCalls.update { it.copy(speaking = active) } } }
        @JavascriptInterface fun trace(payload: String) {
            val data = runCatching { JSONObject(payload) }.getOrNull() ?: return
            val kind = data.optString("kind")
            if (kind !in setOf("transport.state", "speech.policy", "speech.unrequested", "speech.created", "speech.done", "speech.playback_started", "speech.playback_stopped", "speech.playback_cleared", "speech.error")) return
            val safe = JSONObject().put("kind", kind)
            for (key in listOf("state", "response_id", "status", "reason")) {
                val value = data.optString(key)
                if (value.length <= 120 && value.all { it.isLetterOrDigit() || it in "_-.:" }) safe.put(key, value)
            }
            android.util.Log.i("IgorekVoice", safe.toString())
        }
        @JavascriptInterface fun ended() { handler.post { if (!disposed) finishCall() } }
        @JavascriptInterface fun unavailable(message: String) { handler.post { if (!disposed) finishCall(message.take(300)) } }
        @JavascriptInterface fun state(payload: String) { handler.post {
            if (disposed) return@post
            val data = runCatching { JSONObject(payload) }.getOrNull() ?: return@post
            VoiceCalls.update { it.transport(data.optString("phase"), data.optBoolean("connected"), SystemClock.elapsedRealtime()).copy(
                micMode = if (data.optString("micMode") == "continuous") "continuous" else "ptt", pttRecording = data.optBoolean("pttRecording"),
                micMuted = data.optBoolean("micMuted"), speakerMuted = data.optBoolean("speakerMuted"),
                approval = data.optBoolean("approval"), waitingForAgent = data.optBoolean("waitingForAgent"), notice = data.optString("notice").take(300),
            ) }
            if (data.optString("phase") == "error" && !data.optBoolean("active") && !data.optBoolean("starting")) {
                finishCall(data.optString("notice").ifBlank { text("Не удалось установить разговор", "Could not connect the call") })
            }
        } }
        @JavascriptInterface fun request(id: Int, path: String, body: String) {
            if (disposed) return
            val owner = sessionId
            Bridge.voiceRequestAsync(owner, path, body.ifEmpty { null }) { result ->
                handler.post {
                    result.onSuccess { payload ->
                        if (path == "/api/voice/session") {
                            val created = JSONObject(payload.toString()).optString("call_id")
                            if (disposed) Bridge.voiceHangup(owner, created) else callId = created
                        }
                        if (path == "/api/voice/hangup" && JSONObject(body).optString("call_id") == callId) callId = ""
                    }
                    if (disposed) return@post
                    val response = result.getOrNull()?.toString() ?: JSONObject().put("error", result.exceptionOrNull()?.message ?: "Voice unavailable").toString()
                    javascript("nativeReply($id,${if (result.isSuccess) 200 else (result.exceptionOrNull() as? BridgeException)?.status ?: 500},$response)")
                }
            }
        }
    }

    internal fun cancelPushToTalk() { javascript("window.IgorekVoice?.cancelPushToTalk()") }
    internal fun javascript(script: String) { if (!disposed) webView?.evaluateJavascript(script, null) }

    private fun renderState(state: VoiceCallState) {
        if (disposed) return
        if (ringing != state.ringing) {
            ringing = state.ringing
            handler.removeCallbacks(ring)
            if (ringing) ring.run() else tone?.stopTone()
        }
        if (waitingTone != state.holdTone) {
            waitingTone = state.holdTone
            handler.removeCallbacks(hold)
            if (waitingTone) hold.run() else holdTone?.stopTone()
        }
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION, notification())
        refreshOverlay()
    }

    private fun notification(): Notification {
        val state = VoiceCalls.state.value
        val label = when {
            state.approval -> text("Нужно подтверждение — открой разговор", "Approval needed — open the call")
            state.pttRecording -> text("Рация · говори", "Push to talk · recording")
            state.micMode == "ptt" && state.connected -> text("Рация · зажми микрофон в звонке", "Push to talk · hold the call microphone")
            state.micMuted -> text("Микрофон выключен", "Microphone muted")
            state.speaking -> text("Игорёк говорит", "Igorek is speaking")
            state.phase == "reconnecting" -> text("Восстанавливаем связь…", "Reconnecting…")
            state.waitingForAgent -> text("Жду первого ответа Игорька…", "Waiting for Igorek’s first response…")
            state.connected -> text("Голосовой разговор идёт", "Voice call in progress")
            else -> text("Соединяем…", "Connecting…")
        }
        val builder = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher).setContentTitle(text("Игорёк · голосовой разговор", "Igorek · voice call"))
            .setContentText(label).setOngoing(true).setOnlyAlertOnce(true).setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_CALL).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setContentIntent(openIntent())
            .addAction(0, text(if (state.micMode == "ptt") "Открыть рацию" else if (state.micMuted) "Включить микрофон" else "Микрофон выкл.", if (state.micMode == "ptt") "Open push to talk" else if (state.micMuted) "Unmute" else "Mute"), if (state.micMode == "ptt") openIntent() else action(MIC, 42))
            .addAction(0, text("Завершить", "End call"), action(STOP, 43))
        if (state.connectedAt > 0) builder.setWhen(System.currentTimeMillis() - (SystemClock.elapsedRealtime() - state.connectedAt))
            .setUsesChronometer(true).setShowWhen(true)
        return builder.build()
    }

    private fun openIntent() = PendingIntent.getActivity(this, 41,
        Intent(this, MainActivity::class.java).setAction(OPEN).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    private fun action(action: String, code: Int) = PendingIntent.getService(this, code,
        Intent(this, VoiceCallService::class.java).setAction(action), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    @SuppressLint("ClickableViewAccessibility")
    internal fun refreshOverlay() {
        if (disposed) return
        val state = VoiceCalls.state.value
        val manager = getSystemService(WindowManager::class.java)
        val show = state.showOverlay && !VoiceCalls.activityVisible && Settings.canDrawOverlays(this)
        if (!show) {
            cancelBubbleHold()
            bubbleWasRecording = false
            bubblePulse?.cancel(); bubblePulse = null
            bubble?.let { runCatching { manager.removeView(it) } }
            bubble = null
            return
        }
        if (bubble == null) {
            val size = (64 * resources.displayMetrics.density).toInt()
            val params = WindowManager.LayoutParams(size, size, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT).apply { gravity = Gravity.TOP or Gravity.START; x = bubbleX; y = bubbleY }
            val view = VoiceBubbleView(this).apply {
                elevation = 8 * resources.displayMetrics.density
            }

            var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var dragged = false
            val slop = ViewConfiguration.get(this).scaledTouchSlop
            view.setOnTouchListener { _, event ->
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        cancelBubbleHold()
                        downX = event.rawX; downY = event.rawY; startX = params.x; startY = params.y; dragged = false
                        bubbleTouchActive = true
                        if (VoiceCalls.state.value.micMode == "ptt") {
                            bubbleHold = Runnable {
                                if (!disposed && bubbleTouchActive && !dragged && view.isAttachedToWindow && VoiceCalls.state.value.micMode == "ptt") {
                                    bubbleTalkActive = true
                                    VoiceCalls.beginPushToTalk()
                                }
                            }.also { handler.postDelayed(it, 200) }
                        }
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = event.rawX - downX; val dy = event.rawY - downY
                        if (!dragged && (kotlin.math.abs(dx) > slop || kotlin.math.abs(dy) > slop)) { dragged = true; cancelBubbleHold() }
                        if (dragged) {
                            val metrics = resources.displayMetrics
                            params.x = (startX + dx.toInt()).coerceIn(0, (metrics.widthPixels - size).coerceAtLeast(0))
                            params.y = (startY + dy.toInt()).coerceIn(0, (metrics.heightPixels - size - 32).coerceAtLeast(0))
                            bubbleX = params.x; bubbleY = params.y
                            runCatching { manager.updateViewLayout(view, params) }
                        }
                    }
                    MotionEvent.ACTION_UP -> if (bubbleTouchActive || dragged) {
                        bubbleHold?.let(handler::removeCallbacks); bubbleHold = null
                        val talked = bubbleTalkActive
                        bubbleTouchActive = false; bubbleTalkActive = false
                        if (talked) VoiceCalls.endPushToTalk()
                        else if (!dragged) { VoiceCalls.show(); runCatching { openIntent().send() }; view.performClick() }
                    }
                    MotionEvent.ACTION_CANCEL -> cancelBubbleHold()
                }
                true
            }
            bubble = view
            runCatching { manager.addView(view, params) }.onFailure { bubble = null }
        }
        bubble?.let { view ->
            view.contentDescription = if (state.pttRecording) text("Отпусти, чтобы отправить фразу", "Release to send your phrase") else if (state.approval) text("Нужно подтверждение. Открыть разговор", "Approval needed. Open call") else if (state.micMode == "ptt") text("Удерживай, чтобы говорить. Нажми, чтобы открыть разговор", "Hold to speak. Tap to open call") else text("Открыть разговор с Игорьком", "Open Igorek call")
            view.updateState(state)
            if (state.pttRecording && !bubbleWasRecording) view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            bubbleWasRecording = state.pttRecording
            if ((state.speaking || state.pttRecording) && bubblePulse == null) {
                bubblePulse = ValueAnimator.ofFloat(1f, 1.1f).apply {
                    duration = 420; repeatCount = ValueAnimator.INFINITE; repeatMode = ValueAnimator.REVERSE
                    addUpdateListener { view.scaleX = it.animatedValue as Float; view.scaleY = it.animatedValue as Float }
                    start()
                }
            } else if (!state.speaking && !state.pttRecording) {
                bubblePulse?.cancel(); bubblePulse = null
                view.scaleX = 1f; view.scaleY = 1f
            }
            val params = view.layoutParams as WindowManager.LayoutParams
            val flags = params.flags
            params.flags = if (Bridge.screenshotsBlocked.value) flags or WindowManager.LayoutParams.FLAG_SECURE else flags and WindowManager.LayoutParams.FLAG_SECURE.inv()
            runCatching { manager.updateViewLayout(view, params) }
        }
    }

    internal fun finishCall(error: String = "") {
        if (disposed) return
        disposed = true
        if (error.isNotBlank()) VoiceCalls.failed(error) else VoiceCalls.update { VoiceCallState() }
        releaseResources()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun releaseResources() {
        handler.removeCallbacksAndMessages(null)
        ringing = false
        waitingTone = false
        holdTone?.stopTone(); holdTone?.release(); holdTone = null
        tone?.stopTone(); tone?.release(); tone = null
        cancelBubbleHold()
        bubblePulse?.cancel(); bubblePulse = null
        bubble?.let { runCatching { getSystemService(WindowManager::class.java).removeView(it) } }; bubble = null
        Bridge.voiceHangup(sessionId, callId); callId = ""
        webView?.let { view ->
            (view.parent as? ViewGroup)?.removeView(view)
            view.removeJavascriptInterface("Native"); view.stopLoading(); view.destroy()
        }
        webView = null
        focus?.let { audio?.abandonAudioFocusRequest(it) }; focus = null
        audio?.let { if (it.mode == AudioManager.MODE_IN_COMMUNICATION) it.mode = oldAudioMode }; audio = null
        scope.cancel()
        if (VoiceCalls.service === this) VoiceCalls.service = null
    }

    override fun onDestroy() {
        if (!disposed) {
            disposed = true
            VoiceCalls.update { VoiceCallState() }
            releaseResources()
        }
        super.onDestroy()
    }

    private fun text(ru: String, en: String) = AppLocale.text(ru, en)

    companion object {
        internal const val START = "ru.igorek.remote.voice.START"
        internal const val STOP = "ru.igorek.remote.voice.STOP"
        internal const val MIC = "ru.igorek.remote.voice.MIC"
        internal const val OPEN = "ru.igorek.remote.voice.OPEN"
        private const val ORIGIN = "https://igorek-voice.local"
        private const val CHANNEL = "igorek_voice_call"
        private const val NOTIFICATION = 40
    }
}
