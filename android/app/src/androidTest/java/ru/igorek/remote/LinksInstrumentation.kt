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
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.TimelineRow
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

class LinksInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }
    private fun waitFor(label: String, predicate: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 15000
        while (SystemClock.elapsedRealtime() < until) {
            if (predicate()) return
            Thread.sleep(100)
        }
        error("Timed out: $label")
    }
    private fun find(text: String): AccessibilityNodeInfo? {
        fun visit(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            node.refresh()
            for (index in 0 until node.childCount) visit(node.getChild(index))?.let { return it }
            return node.takeIf { it.isVisibleToUser && it.text?.toString()?.contains(text) == true }
        }
        visit(uiAutomation.rootInActiveWindow)?.let { return it }
        for (window in uiAutomation.windows) visit(window.root)?.let { return it }
        return null
    }
    private fun tap(label: String, hold: Long = 60) {
        val node = find(label) ?: error("Missing text: $label")
        val box = Rect(); node.getBoundsInScreen(box)
        sendStatus(0, Bundle().apply { putString("stream", "Tap $label at $box; node=${node.text} children=${node.childCount}\n") })
        val start = SystemClock.uptimeMillis()
        val down = MotionEvent.obtain(start, start, MotionEvent.ACTION_DOWN, box.centerX().toFloat(), box.centerY().toFloat(), 0)
        check(uiAutomation.injectInputEvent(down, true)); down.recycle()
        Thread.sleep(hold)
        val up = MotionEvent.obtain(start, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, box.centerX().toFloat(), box.centerY().toFloat(), 0)
        check(uiAutomation.injectInputEvent(up, true)); up.recycle()
    }
    override fun onStart() {
        val opened = CopyOnWriteArrayList<Intent>()
        val monitor = object : ActivityMonitor() {
            override fun onStartActivity(intent: Intent): ActivityResult? {
                if (intent.action == Intent.ACTION_VIEW) {
                    opened.add(Intent(intent))
                    return ActivityResult(Activity.RESULT_OK, null)
                }
                return null
            }
        }
        addMonitor(monitor)
        uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply { flags = flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS }
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            val markdown = """
                ## [Заголовок](https://example.org/heading)

                - [Источник](https://example.org/article?q=1#part)

                1. [Другой сайт](https://example.net/next)

                https://example.org/my_post_two

                | Тип | Ссылка |
                |---|---|
                | Видео | [Таблица](https://example.org/table) |

                ▸ [Открыть](https://example.org/note)

                Это обычный текст.
            """.trimIndent()
            runOnMainSync {
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { IgorekTheme { TimelineRow(TimelineItem.AssistantText("links-qa", markdown)) } }
            }
            waitFor("rendered answer") { find("Это обычный текст.") != null }
            val checks = listOf("Заголовок" to "https://example.org/heading", "Источник" to "https://example.org/article?q=1#part",
                "Другой сайт" to "https://example.net/next", "https://example.org/my_post_two" to "https://example.org/my_post_two",
                "Таблица" to "https://example.org/table", "Открыть" to "https://example.org/note")
            for ((label, target) in checks) {
                val count = opened.size; tap(label)
                waitFor("browser intent for $label") { opened.size == count + 1 }
                check(opened.last().dataString == target)
                check(opened.last().hasCategory(Intent.CATEGORY_BROWSABLE))
            }
            val count = opened.size; tap("Это обычный текст."); Thread.sleep(300); check(opened.size == count)
            tap("Источник", hold = 850)
            waitFor("selection copy action") { find("Copy") != null || find("Копировать") != null }
            check(opened.size == count)
            tap(if (find("Copy") != null) "Copy" else "Копировать")
            var copied = ""
            runOnMainSync {
                val clipboard = targetContext.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
                copied = clipboard.primaryClip?.getItemAt(0)?.text?.toString().orEmpty()
            }
            check(copied == "Источник") { "Selected text was not copied: $copied" }
            val screenshot = uiAutomation.takeScreenshot() ?: error("Screenshot unavailable")
            val folder = targetContext.getExternalFilesDir("links-qa")!!; folder.mkdirs()
            File(folder, "links.png").outputStream().use { screenshot.compress(Bitmap.CompressFormat.PNG, 100, it) }; screenshot.recycle()
            sendStatus(0, Bundle().apply { putString("stream", "\nMOBILE_LINKS_PASS: six real taps open exact web URLs, plain text inert, long press still copies\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            val folder = targetContext.getExternalFilesDir("links-qa")!!; folder.mkdirs()
            uiAutomation.takeScreenshot()?.let { image ->
                File(folder, "failure.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG,100,it) }; image.recycle()
            }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        } finally { removeMonitor(monitor) }
    }
}
