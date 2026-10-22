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
import ru.igorek.remote.data.BridgeClient
import ru.igorek.remote.data.SessionSummary
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.SessionsScreen
import java.io.File

class SessionPinsInstrumentation : Instrumentation() {
    private var fixture = Bundle()
    override fun onCreate(arguments: Bundle?) { fixture = arguments ?: Bundle(); super.onCreate(arguments); start() }

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

    private fun tap(label: String, hold: Long = 70) {
        waitFor(label) { find(label) != null }
        waitForIdleSync(); Thread.sleep(250)
        val box = Rect(); find(label)!!.getBoundsInScreen(box)
        sendStatus(0, Bundle().apply { putString("stream", "Tap $label bounds=$box hold=$hold\n") })
        val start = SystemClock.uptimeMillis()
        val down = MotionEvent.obtain(start, start, MotionEvent.ACTION_DOWN, box.centerX().toFloat(), box.centerY().toFloat(), 0)
        check(uiAutomation.injectInputEvent(down, true)); down.recycle(); Thread.sleep(hold)
        val up = MotionEvent.obtain(start, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, box.centerX().toFloat(), box.centerY().toFloat(), 0)
        check(uiAutomation.injectInputEvent(up, true)); up.recycle()
    }

    override fun onStart() {
        val output = File(targetContext.getExternalFilesDir(null), "session-pins-qa").apply { mkdirs() }
        try {
            uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
                flags = flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            }
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val activity = startActivitySync(Intent(targetContext, UiAcceptanceActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            val url = checkNotNull(fixture.getString("url"))
            val fingerprint = checkNotNull(fixture.getString("fingerprint"))
            BridgeClient(url, "fixture-session-pins", fingerprint).updateSession("new", pinned = false)
            fun client() = BridgeClient(url, "fixture-session-pins", fingerprint)
            @Suppress("UNCHECKED_CAST")
            val sessions = Bridge.javaClass.getDeclaredField("_sessions").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<List<SessionSummary>>
            val clientField = Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.English)
                clientField.set(Bridge, client())
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    SessionsScreen(onMenu = {}, onOpen = {})
                } } }
            }
            waitFor("initial ordinary chats") { find("Newest chat") != null && find("Older third") != null }
            for ((title, id) in listOf("Older first" to "p1", "Older second" to "p2", "Older third" to "p3")) {
                tap(title, 850); tap("Pin")
                waitFor("$title pinned") { sessions.value.any { it.sessionId == id && it.pinned } && find("Pin") == null }
            }
            check(sessions.value.take(3).map { it.sessionId } == listOf("p3", "p2", "p1"))
            fun top(label: String): Int { val box = Rect(); checkNotNull(find(label)).getBoundsInScreen(box); return box.top }
            waitFor("pins above newest chat") { top("Older third") < top("Older second") && top("Older second") < top("Older first") && top("Older first") < top("Newest chat") }
            fun capture(name: String) { uiAutomation.takeScreenshot()?.let { bitmap ->
                File(output, name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }; bitmap.recycle()
            } }
            capture("three-pins.png")
            runOnMainSync { sessions.value = emptyList(); clientField.set(Bridge, client()); Bridge.refreshSessions() }
            waitFor("pins restored after reconnect") { sessions.value.take(3).map { it.sessionId } == listOf("p3", "p2", "p1") && find("Pinned chat") != null }
            tap("Older second", 850); tap("Unpin")
            waitFor("unpin persisted") { sessions.value.count { it.pinned } == 2 && sessions.value.first { it.sessionId == "p2" }.pinned.not() }
            check(client().sessions().filter { it.pinned }.map { it.sessionId } == listOf("p3", "p1"))
            tap("Refresh")
            waitFor("refreshed unpin") { top("Newest chat") < top("Older second") }
            capture("unpinned.png")
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.Russian)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.Russian) { IgorekTheme {
                    SessionsScreen(onMenu = {}, onOpen = {})
                } } }
            }
            tap("Older second", 850)
            waitFor("Russian action") { find("Закрепить") != null && find("Переименовать") != null && find("В архив") != null }
            capture("russian-pin-menu.png")
            File(output, "result.json").writeText("""{"status":"PASS","checks":["three_native_long_press_pins","pinned_order_above_new_chat","reconnect_persistence","native_unpin","refresh_order","russian_menu","rename_archive_preserved"]}""")
            finish(Activity.RESULT_OK, Bundle().apply { putString("result", "PASS session pins") })
        } catch (error: Throwable) {
            File(output, "failure.txt").writeText(error.stackTraceToString() + "\nLast error: ${Bridge.lastError.value}\nNotifications: ${Bridge.notifications.value}")
            uiAutomation.takeScreenshot()?.let { bitmap -> File(output, "failure.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG,100,it) }; bitmap.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("error", error.stackTraceToString()) })
        }
    }
}
