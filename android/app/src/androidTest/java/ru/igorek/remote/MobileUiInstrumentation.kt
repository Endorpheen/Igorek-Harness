package ru.igorek.remote

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Rect
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.Json
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ErrorObservation
import ru.igorek.remote.data.MessageDto
import ru.igorek.remote.data.NotificationEntry
import ru.igorek.remote.data.SessionTimeline
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.PendingImagePreview
import ru.igorek.remote.ui.SessionScreen
import ru.igorek.remote.ui.TimelineRow
import ru.igorek.remote.ui.storeUploadPreview
import java.io.ByteArrayOutputStream
import java.io.File

class MobileUiInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }

    private fun nodes(label: String): List<AccessibilityNodeInfo> {
        val result = mutableListOf<AccessibilityNodeInfo>()
        fun visit(node: AccessibilityNodeInfo?) {
            if (node == null) return
            if (node.isVisibleToUser && (node.text?.toString() == label || node.contentDescription?.toString() == label)) result += node
            for (index in 0 until node.childCount) visit(node.getChild(index))
        }
        visit(uiAutomation.rootInActiveWindow)
        return result
    }

    private fun waitFor(label: String, check: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 45000
        while (SystemClock.elapsedRealtime() < until) { if (check()) return; Thread.sleep(150) }
        error("Timed out: $label")
    }

    private fun tap(label: String) {
        waitFor(label) { nodes(label).isNotEmpty() }
        val bounds = Rect(); nodes(label).first().getBoundsInScreen(bounds)
        val time = SystemClock.uptimeMillis()
        for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
            val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action,
                bounds.centerX().toFloat(), bounds.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(event, true)); event.recycle(); Thread.sleep(80)
        }
    }

    override fun onStart() {
        val output = targetContext.getExternalFilesDir("mobile-ui-qa")!!; output.mkdirs()
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(
                targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            val source = Bitmap.createBitmap(120, 80, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.rgb(210, 40, 60)) }
            val bytes = ByteArrayOutputStream().also { source.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray(); source.recycle()
            val upload = "a".repeat(32)
            val codec = Json { ignoreUnknownKeys = true }
            val message = codec.decodeFromString<MessageDto>("""{"role":"user","text":"Fixture image message","task_id":"task","message_id":"message","attachments":[{"upload_id":"$upload","name":"fixture.png","size":${bytes.size},"mime_type":"image/png","kind":"image"}]}""")
            storeUploadPreview(targetContext, Bridge.serverUrl.value, "chat-image-fixture", upload, bytes)
            val timeline = SessionTimeline("chat-image-fixture")
            timeline.seedFromHistory(listOf(message.copy(text = "Fixture image message\n\n[attachments: fixture.png]")))
            val sent = timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single()
            val showDraft = mutableStateOf(true)
            fun renderImages() = runOnMainSync {
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    Column(Modifier.padding(16.dp)) {
                        Text("Draft")
                        if (showDraft.value) PendingImagePreview(bytes, "fixture.png", true) { showDraft.value = false }
                        Text("Sent message")
                        TimelineRow(sent, sessionId = "chat-image-fixture")
                    }
                } } }
            }
            renderImages()
            waitFor("two decoded thumbnails") { nodes("Thumbnail: fixture.png").size == 2 }
            check(nodes("Fixture image message").isNotEmpty())
            check(nodes("Fixture image message\n\n[attachments: fixture.png]").isEmpty())
            uiAutomation.takeScreenshot()!!.let { image ->
                val pixels = image.copy(Bitmap.Config.ARGB_8888, false)
                var colored = 0
                for (y in 0 until pixels.height) for (x in 0 until pixels.width) {
                    val pixel = pixels.getPixel(x, y)
                    if (Color.red(pixel) > 160 && Color.green(pixel) < 90 && Color.blue(pixel) < 120) colored++
                }
                pixels.recycle()
                check(colored > 5000) { "Images did not paint: $colored pixels" }
                File(output, "draft-and-sent.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle()
            }
            tap("Remove fixture.png")
            waitFor("sent image remains after draft removal") { nodes("Thumbnail: fixture.png").size == 1 }
            renderImages()
            waitFor("image returns from persisted metadata and cache") { nodes("Thumbnail: fixture.png").size == 1 }
            @Suppress("UNCHECKED_CAST")
            val notifications = Bridge.javaClass.getDeclaredField("_notifications").apply { isAccessible = true }.get(Bridge) as MutableStateFlow<List<NotificationEntry>>
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.English)
                notifications.value = listOf(
                    NotificationEntry("Quiet background fixture", state = "recovered", quiet = true,
                        diagnostic = ErrorObservation(code = "NET.READ_TIMEOUT", component = "mobile-events", phase = "sse_read", owner = "bridge-events:fixture")),
                    NotificationEntry("Visible authorization fixture", diagnostic = ErrorObservation(code = "AUTH.REJECTED", owner = "fixture-auth")),
                )
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    SessionScreen("chat-image-fixture", onMenu = {}, onBack = {}, onOpenSession = {})
                } } }
            }
            tap("Notifications")
            waitFor("real authorization error") { nodes("Visible authorization fixture").isNotEmpty() }
            check(nodes("Quiet background fixture").isEmpty())
            check(Bridge.diagnosticSnapshot().contains("bridge-events:fixture"))
            uiAutomation.takeScreenshot()?.let { image -> File(output, "real-errors-visible.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            sendStatus(0, Bundle().apply { putString("stream", "MOBILE_UI_PASS: draft/sent/reopened images painted; removing draft keeps sent image; quiet timeout hidden while real error and diagnostics remain\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (failure: Throwable) {
            uiAutomation.takeScreenshot()?.let { image -> File(output, "failure.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", failure.stackTraceToString()) })
        }
    }
}
