package ru.igorek.remote

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.CompositionLocalProvider
import kotlinx.coroutines.flow.MutableStateFlow
import ru.igorek.remote.data.*
import ru.igorek.remote.ui.*
import java.io.File

class NotificationOnlyInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }
    private fun find(text: String): AccessibilityNodeInfo? {
        fun visit(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            for (index in 0 until node.childCount) visit(node.getChild(index))?.let { return it }
            return node.takeIf { it.isVisibleToUser && (it.text?.toString() == text || it.contentDescription?.toString() == text) }
        }
        return visit(uiAutomation.rootInActiveWindow)
    }
    private fun await(text: String) {
        val deadline = SystemClock.elapsedRealtime() + 10000
        while (find(text) == null && SystemClock.elapsedRealtime() < deadline) Thread.sleep(100)
        check(find(text) != null) { "Missing UI: $text" }
    }
    private fun tap(text: String) {
        await(text)
        val rect = android.graphics.Rect(); find(text)!!.getBoundsInScreen(rect)
        val now = SystemClock.uptimeMillis()
        for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
            val event = MotionEvent.obtain(now, SystemClock.uptimeMillis(), action, rect.centerX().toFloat(), rect.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(event, true)); event.recycle(); Thread.sleep(100)
        }
    }
    private fun screenshot(name: String) {
        val folder = targetContext.getExternalFilesDir("notification-only-qa")!!; folder.mkdirs()
        uiAutomation.takeScreenshot()?.let { image ->
            File(folder, name).outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle()
        }
    }
    override fun onStart() {
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            waitForIdleSync()
            @Suppress("UNCHECKED_CAST")
            val lastError = Bridge.javaClass.getDeclaredField("_lastError").apply { isAccessible = true }.get(Bridge) as MutableStateFlow<String?>
            runOnMainSync {
                activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
                Bridge.setLanguage(AppLanguage.English)
                lastError.value = "Timeout regression fixture"
                Bridge.reportUiError("Timeout regression fixture", "native-list")
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme { SessionsScreen(onOpen = {}, onMenu = {}) } } }
            }
            await("Sessions");Thread.sleep(400)
            check(find("Timeout regression fixture") == null)
            screenshot("sessions-without-error.png")
            tap("Notifications");await("Timeout regression fixture");screenshot("notification-center.png")
            tap("Close")
            runOnMainSync {
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    SessionChangesDialog(ChangedFilesResponse(listOf(ChangedFileDto("clip.mp4", 90000, 0, "added"), ChangedFileDto("main.py", 12, 3)), ChangedFilesTotalsDto(2, 90012, 3)), {}, {})
                } } }
            }
            await("Video");await("+12")
            check(find("+90000") == null && find("+90012") == null)
            screenshot("media-without-lines.png")
            runOnMainSync {
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme { Column {
                    NotificationCenterButton()
                    TimelineRow(TimelineItem.Status("native-failure", "Task failure fixture", "error", "HTTP 500 regression fixture"))
                } } } }
            }
            await("Notifications");Thread.sleep(400)
            check(find("Task failure fixture") == null && find("HTTP 500 regression fixture") == null)
            tap("Notifications");await("Task failure fixture\nHTTP 500 regression fixture")
            screenshot("task-error-in-center.png")
            finish(Activity.RESULT_OK, Bundle().apply { putString("stream", "NOTIFICATION_ONLY_UI_PASS\n") })
        } catch (failure: Throwable) {
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", "NOTIFICATION_ONLY_UI_FAIL: ${failure.stackTraceToString()}\n") })
        }
    }
}
