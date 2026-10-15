package ru.igorek.remote

import android.Manifest
import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.view.MotionEvent
import android.view.View
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.json.JSONObject
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeEvent
import ru.igorek.remote.data.ApprovalDto
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean
import ru.igorek.remote.service.VoiceCalls
import ru.igorek.remote.service.VoiceCallService
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class VoiceCallInstrumentation : Instrumentation() {
    private var markdownOnly = false
    private var approvalOnly = false
    private var baseUrl = "http://127.0.0.1:48655"
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); baseUrl = arguments?.getString("base_url") ?: baseUrl; markdownOnly = arguments?.getString("markdown_only") == "true"; approvalOnly = arguments?.getString("approval_only") == "true"; start() }
    override fun onStart() {
        try {
            exercise()
            finish(Activity.RESULT_OK, Bundle().apply { putString("stream", "\nVOICE_BACKGROUND_PASS\n") })
        } catch (error: Throwable) {
            runOnMainSync { VoiceCalls.close() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", "\nVOICE_BACKGROUND_FAIL: ${error.stackTraceToString()}\n") })
        }
    }
    private fun progress(message: String) { sendStatus(0, Bundle().apply { putString("stream", "\n$message\n") }) }
    private fun waitFor(message: String, timeout: Long = 30000, condition: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + timeout
        while (SystemClock.elapsedRealtime() < deadline) { if (condition()) return; Thread.sleep(150) }
        error("Timed out: $message; phase=${VoiceCalls.state.value.phase}; error=${VoiceCalls.state.value.error}")
    }
    private fun snapshot(name: String) {
        val directory = File(targetContext.getExternalFilesDir(null), "voice-qa").apply { mkdirs() }
        uiAutomation.takeScreenshot()?.let { bitmap -> File(directory, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }; bitmap.recycle() }
    }
    private fun qa(path: String = "/qa", post: Boolean = false): JSONObject {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 5000; connection.readTimeout = 10000
        if (post) { connection.requestMethod = "POST"; connection.doOutput = true; connection.outputStream.use { it.write("{}".toByteArray()) } }
        return connection.inputStream.bufferedReader().use { JSONObject(it.readText()) }.also { connection.disconnect() }
    }
    private fun tap(x: Float, y: Float, dx: Float = 0f, dy: Float = 0f) {
        val time = SystemClock.uptimeMillis()
        fun inject(action: Int, px: Float, py: Float, delta: Long) {
            val event = MotionEvent.obtain(time, time + delta, action, px, py, 0)
            uiAutomation.injectInputEvent(event, true); event.recycle()
        }
        inject(MotionEvent.ACTION_DOWN, x, y, 0)
        if (dx != 0f || dy != 0f) inject(MotionEvent.ACTION_MOVE, x + dx, y + dy, 150)
        inject(MotionEvent.ACTION_UP, x + dx, y + dy, 300)
    }
    private fun holdMicrophone(cancel: Boolean = false, background: Boolean = false) {
        val located = CountDownLatch(1)
        var x = 0f; var y = 0f
        runOnMainSync {
            val view = VoiceCalls.service!!.webView!!
            val origin = IntArray(2); view.getLocationOnScreen(origin)
            view.evaluateJavascript("JSON.stringify((()=>{const r=document.getElementById('voice-mic').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,w:innerWidth}})())") { result ->
                val point = JSONObject(org.json.JSONTokener(result).nextValue().toString())
                val ratio = view.width / point.getDouble("w")
                x = origin[0] + (point.getDouble("x") * ratio).toFloat()
                y = origin[1] + (point.getDouble("y") * ratio).toFloat()
                located.countDown()
            }
        }
        check(located.await(5, TimeUnit.SECONDS))
        val time = SystemClock.uptimeMillis()
        fun event(action: Int) {
            val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action, x, y, 0)
            event.source = android.view.InputDevice.SOURCE_TOUCHSCREEN
            uiAutomation.injectInputEvent(event, true); event.recycle()
        }
        event(MotionEvent.ACTION_DOWN)
        waitFor("held microphone opens") { VoiceCalls.state.value.pttRecording && !VoiceCalls.state.value.micMuted }
        Thread.sleep(500)
        snapshot(if (cancel) "11-ptt-cancel-held" else "11-ptt-recording")
        if (background) {
            runOnMainSync { targetContext.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://example.org")).setPackage("com.android.chrome").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            waitFor("background cancels held PTT") { !VoiceCalls.activityVisible && VoiceCalls.state.value.micMuted && !VoiceCalls.state.value.pttRecording }
            event(MotionEvent.ACTION_CANCEL)
            runOnMainSync { targetContext.startActivity(Intent(targetContext, MainActivity::class.java).setAction(VoiceCallService.OPEN).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)) }
            waitFor("return after cancelled PTT") { VoiceCalls.activityVisible && VoiceCalls.state.value.expanded }
        } else event(if (cancel) MotionEvent.ACTION_CANCEL else MotionEvent.ACTION_UP)
        waitFor("released microphone closes") { VoiceCalls.state.value.micMuted && !VoiceCalls.state.value.pttRecording }
    }

    private fun holdBubble(x: Float, y: Float, name: String, drag: Boolean = false) {
        val time = SystemClock.uptimeMillis()
        fun send(action: Int, px: Float = x, py: Float = y) {
            val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action, px, py, 0)
            event.source = android.view.InputDevice.SOURCE_TOUCHSCREEN
            uiAutomation.injectInputEvent(event, true); event.recycle()
        }
        send(MotionEvent.ACTION_DOWN)
        waitFor("bubble hold opens PTT") { VoiceCalls.state.value.pttRecording && !VoiceCalls.state.value.micMuted }
        Thread.sleep(400); snapshot(name)
        if (drag) {
            send(MotionEvent.ACTION_MOVE, x + 120f, y + 180f)
            waitFor("drag cancels PTT") { VoiceCalls.state.value.micMuted && !VoiceCalls.state.value.pttRecording }
            send(MotionEvent.ACTION_UP, x + 120f, y + 180f)
        } else send(MotionEvent.ACTION_UP)
        waitFor("bubble release closes PTT") { VoiceCalls.state.value.micMuted && !VoiceCalls.state.value.pttRecording }
    }

    private fun markdownTranscript() {
        runOnMainSync { VoiceCalls.setMicMode("continuous") }
        waitFor("markdown fixture mode ready") { VoiceCalls.state.value.micMode == "continuous" && !VoiceCalls.state.value.micMuted }
        qa("/qa/transcript", true)
        waitFor("markdown fixture task registered") { qa().getInt("messages") == 1 }
        val markdown = """
            ## Главное

            **Жирный текст** и *курсив* — читаемый ответ в звонке.

            - Первый пункт
            - Второй пункт с `кодом`

            ### Сравнение

            | Показатель | Значение |
            |---|---|
            | Точность | 12,5% |
            | Контекст | 100 000 токенов |

            > Важная заметка

            ```python
            print("Пример кода")
            ```

            [Источник](https://example.org)

            <script>window.markdownExecuted=true</script>
            [опасная ссылка](javascript:alert(1))
        """.trimIndent()
        runOnMainSync {
            Bridge.voiceEvents.tryEmit(BridgeEvent(kind = "model.round_response", sessionId = "voice-qa-session", taskId = "voice-qa-task", payload = buildJsonObject { put("text", markdown) }))
        }
        val checked = CountDownLatch(1)
        var result = ""
        Thread.sleep(500)
        runOnMainSync {
            VoiceCalls.service!!.webView!!.evaluateJavascript("JSON.stringify((()=>{const body=document.querySelector('.voice-msg-assistant .voice-msg-body');const code=body?.querySelector('pre code');document.getElementById('conversation').open=true;return {renderer:typeof window.IgorekMarkdown?.renderMarkdown==='function',heading:body?.querySelector('h2')?.textContent,strong:body?.querySelector('strong')?.textContent,list:body?.querySelectorAll('li').length,table:!!body?.querySelector('table'),code:code?.textContent,scriptTags:body?.querySelectorAll('script').length,unsafeLinks:body?.querySelectorAll('a[href^=\"javascript:\"]').length,executed:window.markdownExecuted===true}})())") { result = it; checked.countDown() }
        }
        check(checked.await(5, TimeUnit.SECONDS))
        val proof = JSONObject(org.json.JSONTokener(result).nextValue().toString())
        check(proof.getBoolean("renderer") && proof.getString("heading") == "Главное" && proof.getString("strong") == "Жирный текст")
        check(proof.getInt("list") == 2 && proof.getBoolean("table") && proof.getString("code").contains("Пример кода"))
        check(proof.getInt("scriptTags") == 0 && proof.getInt("unsafeLinks") == 0 && !proof.getBoolean("executed"))
        Thread.sleep(500); snapshot("17-markdown-transcript")
        runOnMainSync { VoiceCalls.service!!.javascript("document.querySelector('.md-pre').scrollIntoView({block:'center'})") }
        Thread.sleep(500); snapshot("18-markdown-code-table")
        runOnMainSync { VoiceCalls.close() }
        waitFor("markdown call stops") { VoiceCalls.service == null }
        progress("MARKDOWN_PASS: packaged common renderer, headings/bold/italic/list/table/code/link; HTML and javascript URLs inert")
    }

    private fun approvalAcrossViews() {
        fun requireApproval(requestId: String) = runOnMainSync {
            Bridge.voiceEvents.tryEmit(BridgeEvent(kind = "approval.required", sessionId = "voice-qa-session", taskId = "voice-qa-task",
                payload = buildJsonObject { put("request_id", requestId); put("tool", "Test action"); put("reason", "Локальная проверка подтверждения") }))
        }
        val originalView = VoiceCalls.service!!.webView
        val originalClock = VoiceCalls.state.value.connectedAt
        requireApproval("chat-decision")
        waitFor("voice approval visible") { VoiceCalls.state.value.approval }
        Thread.sleep(400); snapshot("01-voice-pending")
        runOnMainSync { VoiceCalls.minimize() }
        Thread.sleep(400); snapshot("02-blue-minimized")
        val decided = CountDownLatch(1)
        var failure: Throwable? = null
        runOnMainSync {
            Bridge.decide(ApprovalDto(kind = "tool", requestId = "chat-decision", taskId = "voice-qa-task", sessionId = "voice-qa-session"), "allow_once") {
                failure = it; decided.countDown()
            }
        }
        check(decided.await(10, TimeUnit.SECONDS) && failure == null)
        waitFor("chat decision clears minimized voice approval") { !VoiceCalls.state.value.approval }
        runOnMainSync { VoiceCalls.show() }
        waitFor("voice restored after chat approval") { VoiceCalls.state.value.expanded }
        Thread.sleep(400); snapshot("03-chat-approved-voice-restored")
        requireApproval("voice-decision")
        waitFor("second voice approval visible") { VoiceCalls.state.value.approval }
        val mirrored = AtomicBoolean(false)
        var observer: kotlinx.coroutines.Job? = null
        runOnMainSync {
            observer = CoroutineScope(Dispatchers.Main).launch(start = CoroutineStart.UNDISPATCHED) {
                Bridge.voiceEvents.collect { event ->
                    if (event.kind == "approval.resolved" && event.payload["request_id"]?.toString() == "\"voice-decision\"") mirrored.set(true)
                }
            }
            VoiceCalls.service!!.javascript("document.querySelector('.voice-reject').click()")
        }
        waitFor("voice decision updates shared bridge") { mirrored.get() && !VoiceCalls.state.value.approval }
        observer?.cancel()
        runOnMainSync { VoiceCalls.minimize() }
        targetContext.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://example.org")).setPackage("com.android.chrome").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        waitFor("octopus overlay outside app") { !VoiceCalls.activityVisible && overlay()?.isAttachedToWindow == true }
        Thread.sleep(400); snapshot("04-blue-octopus-overlay")
        val point = IntArray(2)
        runOnMainSync { overlay()!!.getLocationOnScreen(point) }
        holdBubble(point[0] + 70f, point[1] + 70f, "05-octopus-recording", drag = true)
        runOnMainSync { overlay()!!.getLocationOnScreen(point) }
        tap(point[0] + 70f, point[1] + 70f)
        waitFor("octopus tap restores same call") { VoiceCalls.activityVisible && VoiceCalls.state.value.expanded }
        check(VoiceCalls.service!!.webView === originalView && VoiceCalls.state.value.connectedAt == originalClock)
        check(qa().getInt("sessions") == 1)
        runOnMainSync { VoiceCalls.close() }
        waitFor("approval fixture stops") { VoiceCalls.service == null }
        progress("VOICE_APPROVAL_SYNC_PASS: normal chat and voice decisions share state; blue octopus overlay, hold/drag/tap, same call")
    }

    private fun exercise() {

        uiAutomation.grantRuntimePermission(targetContext.packageName, Manifest.permission.RECORD_AUDIO)
        uiAutomation.grantRuntimePermission(targetContext.packageName, Manifest.permission.POST_NOTIFICATIONS)
        runOnMainSync { Bridge.init(targetContext) }
        val paired = CountDownLatch(1)
        var pairingError: Throwable? = null
        runOnMainSync { Bridge.pair(baseUrl, "LOCAL-QA", "Background voice QA", "") { pairingError = it; paired.countDown() } }
        check(paired.await(20, TimeUnit.SECONDS)) { "Pairing timeout" }; check(pairingError == null) { "Pairing failed: $pairingError" }
        val activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        waitFor("visible activity") { VoiceCalls.activityVisible }
        runOnMainSync { VoiceCalls.open(targetContext, "voice-qa-session") }
        waitFor("actual WebRTC connected", 60000) { VoiceCalls.state.value.connected }
        val originalView = VoiceCalls.service!!.webView
        val originalClock = VoiceCalls.state.value.connectedAt
        if (markdownOnly) { markdownTranscript(); return }
        if (approvalOnly) { approvalAcrossViews(); return }
        check(originalClock > 0 && !VoiceCalls.state.value.ringing)
        waitFor("first answer waiting tone") { VoiceCalls.state.value.holdTone }
        waitFor("visible WebView") { originalView?.parent != null && originalView.width > 0 && originalView.height > 0 }
        Thread.sleep(2000); snapshot("01-call-screen")
        check(VoiceCalls.state.value.micMode == "ptt" && VoiceCalls.state.value.micMuted)
        check(qa().optInt("commits") == 0)
        holdMicrophone(cancel = true)
        Thread.sleep(350); check(qa().optInt("commits") == 0)
        holdMicrophone(background = true)
        Thread.sleep(350); check(qa().optInt("commits") == 0)
        holdMicrophone()
        waitFor("one released turn reaches agent") { qa().optInt("commits") == 1 && qa().getInt("messages") == 1 }
        val screenSize = uiAutomation.takeScreenshot()!!
        val menuWidth = screenSize.width.toFloat(); val menuHeight = screenSize.height.toFloat(); screenSize.recycle()
        tap(menuWidth * .79f, menuHeight * .07f)
        Thread.sleep(500); snapshot("12-microphone-modes")
        tap(menuWidth * .65f, menuHeight * .20f)
        waitFor("continuous mode selected from menu") { VoiceCalls.state.value.micMode == "continuous" && !VoiceCalls.state.value.micMuted }
        progress("PTT: default closed, real hold/release and cancel, exactly one committed turn; menu enables continuous mode")
        progress("CONNECTED: real microphone, WebRTC and call timer")
        runOnMainSync { VoiceCalls.open(activity, "other-session"); VoiceCalls.minimize() }
        check(VoiceCalls.state.value.sessionId == "voice-qa-session")
        Thread.sleep(1000); snapshot("02-minimized-in-app")
        check(VoiceCalls.service!!.webView === originalView)
        val before = qa().getJSONArray("peers").getJSONObject(0).getInt("received")
        runOnMainSync { targetContext.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://example.org")).setPackage("com.android.chrome").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        waitFor("background activity") { !VoiceCalls.activityVisible }
        waitFor("system overlay") { overlay()?.isAttachedToWindow == true }
        Thread.sleep(10000)
        val after = qa().getJSONArray("peers").getJSONObject(0).getInt("received")
        check(after > before + 20) { "Microphone packets stopped in background: $before -> $after" }
        check(VoiceCalls.state.value.connected && VoiceCalls.service!!.webView === originalView)
        snapshot("03-over-other-app")
        progress("BACKGROUND: microphone packets $before -> $after while Chrome is open")
        val coordinates = IntArray(2)
        runOnMainSync { overlay()!!.getLocationOnScreen(coordinates) }
        tap(coordinates[0] + 70f, coordinates[1] + 70f, 180f, 300f)
        Thread.sleep(700); snapshot("04-dragged-overlay")
        runOnMainSync { overlay()!!.getLocationOnScreen(coordinates) }
        check(coordinates[0] >= 100 && coordinates[1] >= 300) { "Overlay did not drag" }
        runOnMainSync { targetContext.startService(Intent(targetContext, VoiceCallService::class.java).setAction(VoiceCallService.MIC)) }
        waitFor("notification mute") { VoiceCalls.state.value.micMuted }
        runOnMainSync { targetContext.startService(Intent(targetContext, VoiceCallService::class.java).setAction(VoiceCallService.MIC)) }
        waitFor("notification unmute") { !VoiceCalls.state.value.micMuted }
        check(qa().getInt("sessions") == 1) { "Duplicate call was created" }
        waitFor("ordinary agent session message") { qa().getInt("messages") == 1 }
        runOnMainSync { Bridge.voiceEvents.tryEmit(BridgeEvent(kind = "approval.required", sessionId = "voice-qa-session", taskId = "voice-qa-task", payload = buildJsonObject { put("task_id", "voice-qa-task"); put("request_id", "qa-request"); put("tool", "Test action"); put("reason", "Локальная проверка, реальных действий нет") })) }
        waitFor("approval survives background") { VoiceCalls.state.value.approval }
        snapshot("05-approval-overlay")
        runOnMainSync { overlay()!!.getLocationOnScreen(coordinates) }
        tap(coordinates[0] + 70f, coordinates[1] + 70f)
        waitFor("overlay restores call") { VoiceCalls.activityVisible && VoiceCalls.state.value.expanded }
        check(VoiceCalls.service!!.webView === originalView && VoiceCalls.state.value.connectedAt == originalClock)
        Thread.sleep(1000)
        val approvalVisible = CountDownLatch(1)
        var canApprove = false
        runOnMainSync { VoiceCalls.service!!.webView!!.evaluateJavascript("JSON.stringify((()=>{const r=document.querySelector('.voice-reject').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.width>0})())") { canApprove = it == "\"true\""; approvalVisible.countDown() } }
        check(approvalVisible.await(5, TimeUnit.SECONDS) && canApprove) { "Approval buttons are clipped" }
        snapshot("06-approval-restored")
        runOnMainSync { VoiceCalls.service!!.javascript("document.querySelector('.voice-reject').click()") }
        waitFor("approval decision") { !VoiceCalls.state.value.approval }
        check(qa().getJSONArray("speech").length() == 0) { "User question was spoken" }
        runOnMainSync {
            for (text in listOf("Первый ответ агента", "Следующий ответ агента")) Bridge.voiceEvents.tryEmit(BridgeEvent(kind = "model.round_response", sessionId = "voice-qa-session", taskId = "voice-qa-task", payload = buildJsonObject { put("text", text) }))
        }
        waitFor("both spoken replies finish") { qa().optInt("played") == 2 }
        val speech = qa().getJSONArray("speech")
        check(speech.length() == 2 && qa().optInt("collisions") == 0)
        check(speech.getJSONObject(0).getString("conversation") == "none")
        check(speech.getJSONObject(0).getJSONArray("input").getJSONObject(0).getJSONArray("content").getJSONObject(0).getString("text") == "Первый ответ агента")
        check(!VoiceCalls.state.value.waitingForAgent && !VoiceCalls.state.value.holdTone)
        progress("SPEECH: user input not spoken, two isolated replies played in order, no collision, waiting tone stopped")
        progress("RESTORED: same call, draggable overlay, microphone controls, agent message and approval")
        val monitor = addMonitor(MainActivity::class.java.name, null, false)
        runOnMainSync { activity.recreate() }
        check(waitForMonitorWithTimeout(monitor, 15000) != null) { "Activity did not recreate" }
        removeMonitor(monitor)
        waitFor("recreated activity") { VoiceCalls.activityVisible }
        check(VoiceCalls.service!!.webView === originalView && VoiceCalls.state.value.connectedAt == originalClock)
        Thread.sleep(1000); snapshot("07-after-recreation")
        check(VoiceCalls.state.value.micMode == "continuous")
        tap(menuWidth * .79f, menuHeight * .07f)
        Thread.sleep(350)
        tap(menuWidth * .65f, menuHeight * .145f)
        waitFor("menu returns to push to talk") { VoiceCalls.state.value.micMode == "ptt" && VoiceCalls.state.value.micMuted }
        snapshot("13-returned-to-ptt")
        runOnMainSync { VoiceCalls.minimize() }
        Thread.sleep(500)
        holdBubble(menuWidth * .904f, menuHeight * .252f, "14-in-app-bubble-ptt")
        waitFor("in-app bubble sends one turn") { qa().optInt("commits") == 2 && qa().getInt("messages") == 2 }
        check(!VoiceCalls.state.value.expanded)
        runOnMainSync { targetContext.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://example.org")).setPackage("com.android.chrome").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        waitFor("PTT overlay visible") { !VoiceCalls.activityVisible && overlay()?.isAttachedToWindow == true }
        runOnMainSync { overlay()!!.getLocationOnScreen(coordinates) }
        holdBubble(coordinates[0] + 70f, coordinates[1] + 70f, "15-over-apps-bubble-ptt")
        waitFor("overlay sends one turn") { qa().optInt("commits") == 3 && qa().getInt("messages") == 3 }
        check(!VoiceCalls.activityVisible && !VoiceCalls.state.value.expanded)
        runOnMainSync { overlay()!!.getLocationOnScreen(coordinates) }
        holdBubble(coordinates[0] + 70f, coordinates[1] + 70f, "16-overlay-drag-cancels-ptt", drag = true)
        Thread.sleep(400); check(qa().optInt("commits") == 3)
        check(!VoiceCalls.activityVisible)
        runOnMainSync { overlay()!!.getLocationOnScreen(coordinates) }
        tap(coordinates[0] + 70f, coordinates[1] + 70f)
        waitFor("short bubble tap opens call") { VoiceCalls.activityVisible && VoiceCalls.state.value.expanded }
        check(VoiceCalls.service!!.webView === originalView && VoiceCalls.state.value.connectedAt == originalClock)
        progress("BUBBLE_PTT: in-app and over Chrome hold/release send exactly once, drag cancels without sending, short tap restores same call")
        runOnMainSync { targetContext.startService(Intent(targetContext, VoiceCallService::class.java).setAction(VoiceCallService.STOP)) }
        waitFor("service stops") { VoiceCalls.service == null && !VoiceCalls.state.value.running }
        waitFor("server hangup") { qa().getInt("hangups") >= 1 }
        check(qa().getInt("sessions") == 1)
        check(overlay() == null)
        progress("STOPPED: service, overlay and WebRTC released; one call throughout")
        uiAutomation.executeShellCommand("appops set ${targetContext.packageName} SYSTEM_ALERT_WINDOW deny").use { descriptor ->
            android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor).readBytes()
        }
        runOnMainSync { targetContext.startActivity(Intent(targetContext, MainActivity::class.java).setAction(VoiceCallService.OPEN).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)) }
        waitFor("foreground before second call") { VoiceCalls.activityVisible }
        runOnMainSync { VoiceCalls.open(targetContext, "voice-qa-session") }
        waitFor("second actual connection", 60000) { VoiceCalls.state.value.connected }
        runOnMainSync { VoiceCalls.show() }
        Thread.sleep(500)
        snapshot("10-second-call-before-offer")
        progress("OVERLAY_OFFER: visible=${VoiceCalls.activityVisible}, expanded=${VoiceCalls.state.value.expanded}, package=${uiAutomation.rootInActiveWindow?.packageName}")
        val screen = uiAutomation.takeScreenshot() ?: error("No screenshot")
        val width = screen.width.toFloat(); val height = screen.height.toFloat(); screen.recycle()
        tap(width * .91f, height * .07f)
        Thread.sleep(750)
        check(VoiceCalls.state.value.expanded) { "Permission offer was bypassed" }
        snapshot("09-overlay-permission-offer")
        tap(width * .40f, height * .66f)
        waitFor("minimize without permission") { !VoiceCalls.state.value.expanded }
        check(VoiceCalls.state.value.connected)
        val noOverlayBefore = qa().getJSONArray("peers").getJSONObject(1).getInt("received")
        runOnMainSync { targetContext.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://example.org")).setPackage("com.android.chrome").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        waitFor("background without overlay permission") { !VoiceCalls.activityVisible }
        Thread.sleep(5000)
        check(overlay() == null) { "Overlay appeared without permission" }
        check(VoiceCalls.state.value.connected && VoiceCalls.state.value.micMode == "ptt" && VoiceCalls.state.value.micMuted)
        check(qa().optInt("commits") == 3)
        runOnMainSync { targetContext.startService(Intent(targetContext, VoiceCallService::class.java).setAction(VoiceCallService.STOP)) }
        waitFor("permissionless call stops") { VoiceCalls.service == null }
        progress("NO_OVERLAY_PERMISSION: background call stays connected with closed PTT microphone; notification stop works")
        runOnMainSync { targetContext.startActivity(Intent(targetContext, MainActivity::class.java).setAction(VoiceCallService.OPEN).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)) }
        waitFor("visible again") { VoiceCalls.activityVisible }
        qa("/qa/disable", true)
        runOnMainSync { VoiceCalls.open(targetContext, "voice-qa-session") }
        waitFor("unconfigured voice stops safely") { !VoiceCalls.state.value.running && VoiceCalls.state.value.error.isNotBlank() && VoiceCalls.service == null }
        check(VoiceCalls.service == null && qa().getInt("sessions") == 2)
        Thread.sleep(700); snapshot("08-unavailable-error")
        runOnMainSync { VoiceCalls.close() }
        progress("UNCONFIGURED: no microphone session, no orphan service, visible error")
    }
    private fun overlay(): View? = VoiceCalls.service?.let { service ->
        service.javaClass.getDeclaredField("bubble").apply { isAccessible = true }.get(service) as? View
    }
}
