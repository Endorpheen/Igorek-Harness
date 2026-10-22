package ru.igorek.remote

import android.accessibilityservice.AccessibilityServiceInfo
import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import kotlinx.coroutines.flow.MutableStateFlow
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ErrorObservation
import ru.igorek.remote.data.NotificationEntry
import ru.igorek.remote.data.BridgeEvent
import ru.igorek.remote.data.Prefs
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.data.unreadNotificationCount
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.encodeToString
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.SessionScreen
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

class NotificationReplayInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }

    private fun find(label: String): AccessibilityNodeInfo? {
        fun visit(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            node.refresh()
            for (index in 0 until node.childCount) visit(node.getChild(index))?.let { return it }
            return node.takeIf { it.isVisibleToUser &&
                (it.text?.toString() == label || it.contentDescription?.toString() == label) }
        }
        for (window in uiAutomation.windows) visit(window.root)?.let { return it }
        return visit(uiAutomation.rootInActiveWindow)
    }

    private fun waitFor(label: String, predicate: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 15000
        while (SystemClock.elapsedRealtime() < until) {
            if (predicate()) return
            Thread.sleep(100)
        }
        error("Timed out: $label")
    }

    private fun tap(label: String, holdMs: Long = 60) {
        waitFor(label) { find(label) != null }
        waitForIdleSync()
        Thread.sleep(150)
        val box = Rect(); find(label)!!.getBoundsInScreen(box)
        val time = SystemClock.uptimeMillis()
        val down = MotionEvent.obtain(time, time, MotionEvent.ACTION_DOWN,
            box.centerX().toFloat(), box.centerY().toFloat(), 0)
        check(uiAutomation.injectInputEvent(down, true)); down.recycle()
        Thread.sleep(holdMs)
        val up = MotionEvent.obtain(time, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP,
            box.centerX().toFloat(), box.centerY().toFloat(), 0)
        check(uiAutomation.injectInputEvent(up, true)); up.recycle()
    }

    override fun onStart() {
        try {
            uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
                flags = flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            }
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(
                targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            waitForIdleSync()
            val output = targetContext.getExternalFilesDir("notification-replay-qa")!!.apply { mkdirs() }
            @Suppress("UNCHECKED_CAST")
            val notifications = Bridge.javaClass.getDeclaredField("_notifications").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<List<NotificationEntry>>
            @Suppress("UNCHECKED_CAST")
            val timeline = Bridge.javaClass.getDeclaredField("_timeline").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<List<TimelineItem>>
            val handle = Bridge.javaClass.getDeclaredMethod("handleEvent", BridgeEvent::class.java).apply { isAccessible = true }
            val load = Bridge.javaClass.getDeclaredMethod("loadNotificationHistory").apply { isAccessible = true }
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.English)
                Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, null)
                (Bridge.javaClass.getDeclaredField("prefs").apply { isAccessible = true }.get(Bridge) as Prefs).lastSeq = 0
                notifications.value = emptyList()
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            }
            fun render(session: String) {
                runOnMainSync {
                    timeline.value = (1..10).map { index -> TimelineItem.Status(
                        "finish-$session-task-$index-$index", "Task finished with status: fail", level = "error", taskId = "$session-task-$index") }
                    activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                        SessionScreen(session, onMenu = {}, onBack = {}, onOpenSession = {})
                    } } }
                }
                waitForIdleSync(); Thread.sleep(500)
            }
            fun screenshot(name: String) {
                uiAutomation.takeScreenshot()?.let { image -> File(output, name).outputStream().use {
                    image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            }
            fun deliver(seq: Long, error: ErrorObservation, kind: String = "task.error") {
                val payload = buildJsonObject {
                    put("status", "fail"); put("diagnostic", kotlinx.serialization.json.Json.parseToJsonElement(
                        kotlinx.serialization.json.Json.encodeToString(error)))
                }
                runOnMainSync { handle.invoke(Bridge, BridgeEvent(kind = kind, seq = seq, ts = error.observedAt,
                    sessionId = error.sessionId, taskId = error.taskId, payload = payload)) }
                waitForIdleSync()
            }
            render("history-a")
            screenshot("history-a.png")
            check(notifications.value.isEmpty()) {
                "Opening saved history created ${notifications.value.size} false new notifications"
            }
            render("history-b"); render("history-a")
            check(notifications.value.isEmpty())
            val failure = ErrorObservation(eventId = "real-live-event", code = "HTTP.SERVICE_UNAVAILABLE",
                component = "model", owner = "model:live-fixture", httpStatus = 500,
                taskId = "real-task", sessionId = "history-a", phase = "http_body")
            deliver(101, failure)
            waitFor("new live failure") { notifications.value.size == 1 && unreadNotificationCount(notifications.value) == 1 }
            val id = notifications.value.single().id
            tap("Notifications"); tap("Mark read"); tap("Close")
            check(unreadNotificationCount(notifications.value) == 0)
            repeat(3) { render("history-b"); render("history-a") }
            check(notifications.value.single().id == id && notifications.value.single().count == 1 && notifications.value.single().read)
            runOnMainSync { load.invoke(Bridge) }
            check(notifications.value.single().id == id && notifications.value.single().read)
            deliver(101, failure)
            check(notifications.value.size == 1 && notifications.value.single().read && notifications.value.single().count == 1)
            deliver(102, failure, "network.recovered")
            check(notifications.value.single().state == "recovered")
            deliver(101, failure)
            check(notifications.value.size == 1 && notifications.value.single().state == "recovered")
            tap("Notifications"); tap("Clear"); tap("Close")
            deliver(101, failure)
            repeat(2) { render("history-b"); render("history-a") }
            check(notifications.value.isEmpty())
            tap("Notifications")
            waitFor("no resurrected errors") { find("No active error notifications") != null }
            screenshot("after-read-clear-and-switch.png")
            tap("Close")
            deliver(103, failure.copy(eventId = "genuine-new-failure", taskId = "new-task", owner = "model:new-task"))
            check(notifications.value.size == 1 && !notifications.value.single().read)
            runOnMainSync { Bridge.reportUiError("Fixture upload failed", "session-input:history-a") }
            check(notifications.value.size == 2)
            val snapshot = Bridge.diagnosticSnapshot()
            check(snapshot.contains("ui:session-input:history-a") && snapshot.contains("genuine-new-failure"))
            File(output, "journal.json").writeText(snapshot)
            tap("Notifications"); screenshot("genuine-new-errors.png")
            tap("Close")
            runOnMainSync { notifications.value = emptyList() }
            fun toolEvent(seq: Long, status: String, service: String = "fixture", invalid: Boolean = false): BridgeEvent =
                BridgeEvent(kind = "tool.result", seq = seq, ts = 1792629242L + seq - 201,
                    taskId = "tool-fixture", sessionId = "history-a", payload = buildJsonObject {
                        put("status", status); put("tool_name", "host_service"); put("tool_use_id", "fixture-call-$seq")
                        put("tool_input", buildJsonObject {
                            put("action", "files"); put("service", service); if (invalid) put("file_id", "unexpected-field")
                        })
                        put("result_preview", if (invalid) """{"error":"invalid_service_fields"}""" else """{"files":[]}""")
                    })
            val original = toolEvent(201, "error", invalid = true)
            runOnMainSync { handle.invoke(Bridge, original) }
            waitFor("typed tool failure") { notifications.value.singleOrNull()?.diagnostic?.reasonCode == "invalid_service_fields" }
            val toolEntry = notifications.value.single()
            check(toolEntry.text.contains("host_service") && toolEntry.text.contains("invalid set of fields"))
            check(toolEntry.diagnostic!!.observedAt == 1792629242000L)
            tap("Notifications")
            waitFor("visible concrete cause") { find(toolEntry.text) != null }
            screenshot("tool-cause-readable.png")
            tap("Mark read"); tap("Close")
            runOnMainSync { handle.invoke(Bridge, toolEvent(202, "done", service = "different")) }
            check(notifications.value.single().state != "recovered")
            runOnMainSync { handle.invoke(Bridge, toolEvent(203, "done")) }
            check(notifications.value.single().state == "recovered" && unreadNotificationCount(notifications.value) == 0)
            tap("Notifications")
            waitFor("recovered tool hidden") { find("No active error notifications") != null }
            screenshot("tool-recovered-hidden.png"); tap("Close")
            val legacyError = toolEntry.diagnostic!!.copy(code = "ERROR.UNKNOWN", component = "task", observedAt = 1792629242L,
                toolName = "", toolUseId = "", toolAction = "", service = "", reasonCode = "", operationKey = "", sourceSequence = null)
            val legacy = toolEntry.copy(text = "Unknown old failure", state = "needs_owner", read = true,
                diagnostic = legacyError, events = listOf(legacyError))
            runOnMainSync {
                (Bridge.javaClass.getDeclaredField("prefs").apply { isAccessible = true }.get(Bridge) as Prefs)
                    .notificationHistory = kotlinx.serialization.json.Json.encodeToString(listOf(legacy))
                load.invoke(Bridge)
                handle.invoke(Bridge, original)
                handle.invoke(Bridge, toolEvent(203, "done"))
                load.invoke(Bridge)
            }
            check(notifications.value.single().id == legacy.id && notifications.value.single().read && notifications.value.single().state == "recovered")
            check(notifications.value.single().diagnostic!!.reasonCode == "invalid_service_fields")
            val report = ru.igorek.remote.data.incidentDiagnosticReport(notifications.value.single(), "fixture", AppLanguage.English)
            check(report.contains("2026-10-22T00:34:02Z") && report.contains("tool_retry_succeeded") && !report.contains("1970-"))
            File(output, "tool-recovery-report.json").writeText(report)
            tap("Notifications")
            waitFor("legacy incident stays hidden") { find("No active error notifications") != null }
            screenshot("legacy-tool-reconciled.png")
            tap("Close")
            val code = "<main>fixture</main>"
            val reasoning = "Plan with inline `code`.\n```html\n" + code + "\n```\n```js\nconst fixture = 1;"
            val durationTimeline = ru.igorek.remote.data.SessionTimeline("history-a")
            durationTimeline.apply(BridgeEvent(kind = "task.start", seq = 401, ts = 1792629000,
                sessionId = "history-a", taskId = "duration-fixture"))
            durationTimeline.apply(BridgeEvent(kind = "task.finish", seq = 402, ts = 1792629134,
                sessionId = "history-a", taskId = "duration-fixture", payload = buildJsonObject { put("status", "ok") }))
            runOnMainSync {
                timeline.value = listOf(TimelineItem.Reasoning("reasoning-markdown-fixture", reasoning)) + durationTimeline.snapshot()
                activity.setContent { androidx.compose.runtime.key("presentation-fixture") {
                    CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                        SessionScreen("history-a", onMenu = {}, onBack = {}, onOpenSession = {})
                    } }
                } }
            }
            waitFor("completed task elapsed time") { find("Task completed · 2 min 14 s") != null }
            check(find("html") == null)
            tap("show all")
            waitFor("reasoning HTML code block") { find("html") != null && find(code) != null }
            waitFor("unfinished reasoning JS code block") { find("js") != null && find("const fixture = 1;") != null }
            screenshot("reasoning-code-and-task-duration.png")
            waitForIdleSync()
            uiAutomation.waitForIdle(500, 5000)
            val htmlBox = Rect(); find("html")!!.getBoundsInScreen(htmlBox)
            val copyNodes = mutableListOf<AccessibilityNodeInfo>()
            fun collectCopies(node: AccessibilityNodeInfo?) {
                if (node == null) return
                node.refresh()
                if (node.isVisibleToUser && node.contentDescription?.toString() == "Copy") copyNodes.add(node)
                for (index in 0 until node.childCount) collectCopies(node.getChild(index))
            }
            for (window in uiAutomation.windows) collectCopies(window.root)
            collectCopies(uiAutomation.rootInActiveWindow)
            val copy = copyNodes.minByOrNull { node -> val box = Rect(); node.getBoundsInScreen(box); kotlin.math.abs(box.centerY() - htmlBox.centerY()) }
                ?: error("Code copy button missing")
            val copyBox = Rect(); copy.getBoundsInScreen(copyBox)
            check(kotlin.math.abs(copyBox.centerY() - htmlBox.centerY()) < 80) {
                "Code copy row mismatch: label=$htmlBox candidates=" + copyNodes.joinToString { node ->
                    val box = Rect(); node.getBoundsInScreen(box); box.toString()
                }
            }
            val inputTime = SystemClock.uptimeMillis()
            val down = MotionEvent.obtain(inputTime, inputTime, MotionEvent.ACTION_DOWN, copyBox.centerX().toFloat(), copyBox.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(down, true)); down.recycle(); Thread.sleep(60)
            val up = MotionEvent.obtain(inputTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, copyBox.centerX().toFloat(), copyBox.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(up, true)); up.recycle(); waitForIdleSync()
            runOnMainSync {
                val clipboard = targetContext.getSystemService(android.content.ClipboardManager::class.java)
                check(clipboard.primaryClip?.getItemAt(0)?.coerceToText(targetContext)?.toString() == code)
            }
            check(find("html") != null)

            sendStatus(0, Bundle().apply { putString("stream",
                "NOTIFICATION_REPLAY_PASS: ten old failures remain passive across A/B switches, real live failure retained/read/restored, replay suppressed after recovery and clear, new failures visible, UI input errors share the report journal; tool cause/time retained, matching retry hidden, legacy incident reconciled without duplicate; reasoning code rendered/copied and task elapsed time visible\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            val output = targetContext.getExternalFilesDir("notification-replay-qa")!!.apply { mkdirs() }
            runCatching {
                val tree = StringBuilder()
                fun visit(node: AccessibilityNodeInfo?, depth: Int = 0) {
                    if (node == null || depth > 30 || tree.length > 60000) return
                    node.refresh()
                    val box = Rect(); node.getBoundsInScreen(box)
                    tree.append(" ".repeat(depth)).append(node.className).append(" text=").append(node.text)
                        .append(" description=").append(node.contentDescription).append(" bounds=").append(box).append('\n')
                    for (index in 0 until node.childCount) visit(node.getChild(index), depth + 1)
                }
                for (window in uiAutomation.windows) visit(window.root)
                File(output, "failure-state.txt").writeText(tree.toString())
            }
            uiAutomation.takeScreenshot()?.let { image -> File(output, "failure.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        }
    }
}
