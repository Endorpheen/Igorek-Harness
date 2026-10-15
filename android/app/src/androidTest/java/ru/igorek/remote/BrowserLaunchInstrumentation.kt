package ru.igorek.remote

import android.app.Activity
import android.app.Instrumentation
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.ContextWrapper
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
import ru.igorek.remote.ui.markdown.openWebLink
import java.io.File

class BrowserLaunchInstrumentation : Instrumentation() {
    private var base = ""
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        base = arguments?.getString("base_url").orEmpty()
        start()
    }
    private fun find(text: String): AccessibilityNodeInfo? {
        fun visit(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            for (index in 0 until node.childCount) visit(node.getChild(index))?.let { return it }
            return node.takeIf { it.isVisibleToUser && (it.text?.toString()?.contains(text, true) == true || it.contentDescription?.toString()?.contains(text, true) == true) }
        }
        return visit(uiAutomation.rootInActiveWindow)
    }
    private fun tap(node: AccessibilityNodeInfo) {
        val rect = Rect(); node.getBoundsInScreen(rect)
        val start = SystemClock.uptimeMillis()
        val down = MotionEvent.obtain(start, start, MotionEvent.ACTION_DOWN, rect.centerX().toFloat(), rect.centerY().toFloat(), 0)
        uiAutomation.injectInputEvent(down, true); down.recycle(); Thread.sleep(70)
        val up = MotionEvent.obtain(start, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, rect.centerX().toFloat(), rect.centerY().toFloat(), 0)
        uiAutomation.injectInputEvent(up, true); up.recycle()
    }
    private fun waitFor(label: String, timeout: Long = 25000, predicate: () -> Boolean) {
        val end = SystemClock.elapsedRealtime() + timeout
        while (SystemClock.elapsedRealtime() < end) {
            if (predicate()) return
            Thread.sleep(150)
        }
        error("Timed out: $label")
    }
    private fun browserPage(marker: String) {
        waitFor("real browser page $marker", 60000) {
            if (find(marker) != null) true else {
                listOf("Accept & continue", "Use without an account", "No thanks", "Got it", "Not now", "Continue", "Don’t allow", "Don't allow").firstNotNullOfOrNull { find(it) }?.let(::tap)
                false
            }
        }
        check(uiAutomation.rootInActiveWindow?.packageName != targetContext.packageName)
    }
    override fun onStart() {
        try {
            check(base.startsWith("http://127.0.0.1:"))
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            var activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            runOnMainSync {
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { IgorekTheme { TimelineRow(TimelineItem.AssistantText("browser-qa", "[Открыть проверку]($base/actual-link)")) } }
            }
            waitFor("link in answer") { find("Открыть проверку") != null }
            tap(find("Открыть проверку")!!)
            browserPage("IGOREK_BROWSER_DIRECT_OK")
            activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)) as ComponentActivity
            var attempts = 0
            val context = object : ContextWrapper(activity) {
                override fun startActivity(intent: Intent) {
                    attempts++
                    if (attempts == 1) throw ActivityNotFoundException("Simulated unavailable preferred handler")
                    super.startActivity(intent)
                }
            }
            runOnMainSync { openWebLink(context, "$base/fallback-link") }
            browserPage("IGOREK_BROWSER_FALLBACK_OK")
            check(attempts == 2) { "Fallback did not run: $attempts" }
            val image = uiAutomation.takeScreenshot() ?: error("Screenshot unavailable")
            val folder = targetContext.getExternalFilesDir("browser-qa")!!; folder.mkdirs()
            File(folder, "browser.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle()
            sendStatus(0, Bundle().apply { putString("stream", "\nREAL_BROWSER_LAUNCH_PASS: click opened browser page; missing preferred handler recovered through system fallback\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            val folder = targetContext.getExternalFilesDir("browser-qa")!!; folder.mkdirs()
            uiAutomation.takeScreenshot()?.let { image -> File(folder,"failure.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG,100,it) };image.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        }
    }
}
