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
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.SessionScreen
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

class NotificationsInstrumentation : Instrumentation() {
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
        uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
            flags = flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
        }
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(
                targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            waitForIdleSync()
            val repeatedText = "Notification regression fixture"
            val error = ErrorObservation(code = "NET.CONNECTION_RESET", owner = "notification-fixture")
            val entries = listOf(
                NotificationEntry(repeatedText, "CONNECTION", firstSeen = 100, lastSeen = 100,
                    id = "recovered-incident", state = "recovered", recoveredAt = 200,
                    diagnostic = error, events = listOf(error)),
                NotificationEntry(repeatedText, "CONNECTION", firstSeen = 300, lastSeen = 300,
                    id = "new-incident", diagnostic = error.copy(eventId = "next-outage")),
            )
            @Suppress("UNCHECKED_CAST")
            val notifications = Bridge.javaClass.getDeclaredField("_notifications").apply {
                isAccessible = true
            }.get(Bridge) as MutableStateFlow<List<NotificationEntry>>
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.English)
                notifications.value = entries
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent {
                    CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) {
                        IgorekTheme {
                            SessionScreen("notification-fixture", onMenu = {}, onBack = {}, onOpenSession = {})
                        }
                    }
                }
            }
            tap("Notifications")
            waitFor("notification dialog") { find("Mark read") != null && find(repeatedText) != null }
            waitForIdleSync()
            check(notifications.value.map { it.id }.toSet().size == 2)
            val folder = targetContext.getExternalFilesDir("notifications-qa")!!; folder.mkdirs()
            uiAutomation.takeScreenshot()?.let { image ->
                File(folder, "notifications.png").outputStream().use {
                    image.compress(Bitmap.CompressFormat.PNG, 100, it)
                }; image.recycle()
            }
            val feedback = CopyOnWriteArrayList<String>()
            uiAutomation.setOnAccessibilityEventListener { event ->
                if (event.eventType == AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED) {
                    feedback.addAll(event.text.map { it.toString() })
                }
            }
            tap(repeatedText, holdMs = 850)
            waitFor("visible diagnostic copy confirmation") {
                feedback.any { it.contains("Diagnostics copied to clipboard") } ||
                    find("Diagnostics copied to clipboard") != null
            }
            var copied = ""
            runOnMainSync {
                val clipboard = targetContext.getSystemService(android.content.Context.CLIPBOARD_SERVICE)
                    as android.content.ClipboardManager
                copied = clipboard.primaryClip?.getItemAt(0)?.text?.toString().orEmpty()
            }
            check(copied.contains("recovered-incident") && copied.contains("new-incident"))
            uiAutomation.setOnAccessibilityEventListener(null)
            tap("Mark read")
            waitFor("acknowledged incidents") { notifications.value.all { it.read } }
            check(notifications.value.first().state == "recovered")
            check(notifications.value.last().state == "observed")
            tap("Close")
            waitFor("closed dialog") { find("Mark read") == null }
            tap("Notifications")
            tap("Send diagnostics")
            waitFor("support preview") { find("Share report") != null }
            tap("Close")
            waitFor("return to notifications") { find("Mark read") != null }
            check(notifications.value.size == 2)
            tap("Clear")
            waitFor("cleared notification list") { notifications.value.isEmpty() }
            waitForIdleSync()
            sendStatus(0, Bundle().apply { putString("stream",
                "NOTIFICATIONS_PASS: repeated-text incidents open, long-press copies with visible confirmation, acknowledge, reopen, preview, cancel and clear\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            val folder = targetContext.getExternalFilesDir("notifications-qa")!!; folder.mkdirs()
            uiAutomation.takeScreenshot()?.let { image ->
                File(folder, "failure.png").outputStream().use {
                    image.compress(Bitmap.CompressFormat.PNG, 100, it)
                }; image.recycle()
            }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        }
    }
}
