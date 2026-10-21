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
            sendStatus(0, Bundle().apply { putString("stream",
                "NOTIFICATION_REPLAY_PASS: ten old failures remain passive across A/B switches, real live failure retained/read/restored, replay suppressed after recovery and clear, new failures visible, UI input errors share the report journal\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            val output = targetContext.getExternalFilesDir("notification-replay-qa")!!.apply { mkdirs() }
            uiAutomation.takeScreenshot()?.let { image -> File(output, "failure.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        }
    }
}
