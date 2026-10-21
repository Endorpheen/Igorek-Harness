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
import androidx.compose.runtime.CompositionLocalProvider
import kotlinx.coroutines.flow.MutableStateFlow
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ContextUsage
import ru.igorek.remote.data.SessionDetail
import ru.igorek.remote.ui.ControlTopBar
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import java.io.File

class ContextUsageInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }

    private fun find(label: String): AccessibilityNodeInfo? {
        fun visit(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            node.refresh()
            for (index in 0 until node.childCount) visit(node.getChild(index))?.let { return it }
            return node.takeIf { it.isVisibleToUser && (it.text?.toString() == label || it.contentDescription?.toString() == label) }
        }
        for (window in uiAutomation.windows) visit(window.root)?.let { return it }
        return visit(uiAutomation.rootInActiveWindow)
    }

    private fun waitFor(label: String, predicate: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 20000
        while (SystemClock.elapsedRealtime() < until) { if (predicate()) return; Thread.sleep(100) }
        error("Timed out: $label")
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
            @Suppress("UNCHECKED_CAST")
            val detail = Bridge.javaClass.getDeclaredField("_sessionDetail").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<SessionDetail?>
            @Suppress("UNCHECKED_CAST")
            val usage = Bridge.javaClass.getDeclaredField("_contextUsage").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<ContextUsage>
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.English)
                detail.value = SessionDetail(sessionId = "local-context-fixture", contextWindow = 64000)
                usage.value = ContextUsage(window = 128000, lastInput = 32000)
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    ControlTopBar(title = "Local fixture", connection = "online", compactStatus = true,
                        showNotifications = false, onMenu = {})
                } } }
            }
            waitFor("status button") { find("Local fixture — Online") != null }
            val bounds = Rect(); find("Local fixture — Online")!!.getBoundsInScreen(bounds)
            val time = SystemClock.uptimeMillis()
            for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
                val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action, bounds.centerX().toFloat(), bounds.centerY().toFloat(), 0)
                check(uiAutomation.injectInputEvent(event, true)); event.recycle(); Thread.sleep(80)
            }
            waitFor("local context percentage") { find("50% free") != null }
            check(find("32,000 of 64,000 tokens remaining") != null)
            val output = File(targetContext.getExternalFilesDir(null), "context-usage-qa").apply { mkdirs() }
            fun capture(name: String) { uiAutomation.takeScreenshot()?.let { bitmap ->
                File(output, name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }; bitmap.recycle()
            } }
            capture("local-64k.png")
            runOnMainSync { detail.value = detail.value!!.copy(contextWindow = 128000) }
            waitFor("changed server limit") { find("75% free") != null }
            check(find("96,000 of 128,000 tokens remaining") != null)
            capture("local-128k.png")
            runOnMainSync { detail.value = detail.value!!.copy(contextWindow = 64000); usage.value = ContextUsage() }
            waitFor("unknown usage") { find("No token data yet") != null && find("Size: 64,000 tokens") != null }
            check(find("100% free") == null)
            capture("unknown-usage.png")
            runOnMainSync { detail.value = null; usage.value = ContextUsage(window = 200000, lastInput = 50000) }
            waitFor("cloud event fallback") { find("75% free") != null && find("150,000 of 200,000 tokens remaining") != null }
            File(output, "result.json").writeText("""{"status":"PASS","checks":["live_local_64k","server_changed_128k","unknown_usage","cloud_event_fallback"]}""")
            finish(Activity.RESULT_OK, Bundle().apply { putString("result", "PASS context usage") })
        } catch (error: Throwable) {
            runCatching {
                val output = File(targetContext.getExternalFilesDir(null), "context-usage-qa").apply { mkdirs() }
                val text = StringBuilder("language=${Bridge.language.value} window=${Bridge.sessionDetail.value?.contextWindow} usage=${Bridge.contextUsage.value}\n")
                fun visit(node: AccessibilityNodeInfo?, depth: Int = 0) {
                    if (node == null || depth > 30 || text.length > 60000) return
                    text.append(" ".repeat(depth)).append(node.className).append(" text=").append(node.text)
                        .append(" description=").append(node.contentDescription).append(" visible=").append(node.isVisibleToUser).append('\n')
                    for (index in 0 until node.childCount) visit(node.getChild(index), depth + 1)
                }
                for (window in uiAutomation.windows) visit(window.root)
                visit(uiAutomation.rootInActiveWindow)
                File(output, "failure-state.txt").writeText(text.toString())
                uiAutomation.takeScreenshot()?.let { bitmap -> File(output, "failure.png").outputStream().use {
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }; bitmap.recycle() }
            }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("error", error.stackTraceToString()) })
        }
    }
}
