package ru.igorek.remote

import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.Surface
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.SessionBrowserPanel
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONObject

class BrowserPreviewInstrumentation : Instrumentation() {
    private var base = "http://127.0.0.1:48661"
    private var pairCode = ""
    private var tag = "phone"
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        base = arguments?.getString("base_url") ?: base
        pairCode = arguments?.getString("pair_code").orEmpty()
        tag = arguments?.getString("tag") ?: tag
        start()
    }
    private fun nodes(): List<AccessibilityNodeInfo> {
        val result = mutableListOf<AccessibilityNodeInfo>()
        fun visit(node: AccessibilityNodeInfo?) {
            if (node == null) return
            node.refresh()
            result += node
            for (i in 0 until node.childCount) visit(node.getChild(i))
        }
        visit(uiAutomation.rootInActiveWindow)
        return result
    }
    private fun find(label: String) = nodes().firstOrNull {
        it.isVisibleToUser && (it.text?.toString()?.contains(label) == true || it.contentDescription?.toString() == label)
    }
    private fun waitFor(label: String, condition: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 20000
        while (SystemClock.elapsedRealtime() < until) {
            if (condition()) return
            Thread.sleep(150)
        }
        error("Timed out: $label")
    }
    private fun locate(label: String): AccessibilityNodeInfo {
        for (i in 0..8) {
            find(label)?.let { return it }
            nodes().firstOrNull { it.isVisibleToUser && it.isScrollable }?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
            Thread.sleep(300)
        }
        error("Missing control: $label")
    }
    private fun tap(label: String) {
        var node: AccessibilityNodeInfo? = nodes().firstOrNull { it.isVisibleToUser && (it.text?.toString() == label || it.contentDescription?.toString() == label) } ?: locate(label)
        while (node != null && !node.isClickable) node = node.parent
        check(node != null && node.isEnabled && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) { "Cannot click $label" }
        Thread.sleep(350)
    }
    private fun qa(path: String = "/qa"): JSONObject {
        val connection = URL(base + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 3000; connection.readTimeout = 5000
        try { return connection.inputStream.bufferedReader().use { JSONObject(it.readText()) } }
        finally { connection.disconnect() }
    }
    private fun screenshot(name: String) {
        val folder = File(targetContext.getExternalFilesDir(null), "browser-qa").apply { mkdirs() }
        val image = uiAutomation.takeScreenshot() ?: error("Screenshot unavailable")
        File(folder, "$tag-$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
        image.recycle()
    }
    private fun progress(message: String) = sendStatus(0, Bundle().apply { putString("stream", "\n$message\n") })
    override fun onStart() {
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val accessibility = uiAutomation.serviceInfo
            accessibility.flags = accessibility.flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
            uiAutomation.serviceInfo = accessibility
            runOnMainSync { Bridge.init(targetContext) }
            val paired = CountDownLatch(1)
            var failure: Throwable? = null
            runOnMainSync { Bridge.pair(base, pairCode, "Browser QA", "") { failure = it; paired.countDown() } }
            check(paired.await(15, TimeUnit.SECONDS) && failure == null) { "Local fixture pairing failed" }
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            runOnMainSync { activity.setContent { IgorekTheme { Surface { SessionBrowserPanel({}) } } } }
            waitFor("second tab preview") { find("Example Two") != null }
            check(find("Открой браузер") == null)
            screenshot("01-preview")
            tap("Example Two")
            waitFor("tab menu") { find("Example One") != null }
            screenshot("02-tabs")
            tap("Example One")
            waitFor("first desktop tab") { val state = qa(); state.getString("active_id") == state.getString("first_id") && state.getString("ui_active_id") == state.getString("first_id") }
            waitFor("first tab frame") { find("Example One") != null }
            screenshot("03-selected")
            tap("Example One"); tap("Example Two")
            waitFor("second desktop tab") { val state = qa(); state.getString("active_id") == state.getString("second_id") && state.getString("ui_active_id") == state.getString("second_id") }
            qa("/qa/done")
            finish(0, Bundle().apply { putString("stream", "BROWSER_PREVIEW_NATIVE_PASS: actual JPEG, compact tab menu, both desktop selections synchronized") })
        } catch (failure: Throwable) {
            finish(1, Bundle().apply { putString("stream", failure.stackTraceToString()) })
        }
    }
}
