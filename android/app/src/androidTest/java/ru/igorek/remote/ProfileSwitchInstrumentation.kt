package ru.igorek.remote

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
import kotlinx.serialization.json.jsonPrimitive
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeClient
import ru.igorek.remote.data.BridgeException
import ru.igorek.remote.data.SessionSummary
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.SessionScreen
import java.io.File

class ProfileSwitchInstrumentation : Instrumentation() {
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

    private fun editor(): AccessibilityNodeInfo? {
        fun visit(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            if (node.isEditable && node.isVisibleToUser) return node
            for (index in 0 until node.childCount) visit(node.getChild(index))?.let { return it }
            return null
        }
        return visit(uiAutomation.rootInActiveWindow)
    }

    private fun waitFor(label: String, predicate: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 45000
        while (SystemClock.elapsedRealtime() < until) { if (predicate()) return; Thread.sleep(150) }
        error("Timed out: $label")
    }

    private fun tap(label: String) {
        waitFor(label) { find(label) != null }
        val box = Rect(); find(label)!!.getBoundsInScreen(box)
        val time = SystemClock.uptimeMillis()
        for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
            val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action,
                box.centerX().toFloat(), box.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(event, true)); event.recycle(); Thread.sleep(80)
        }
    }

    private fun setDraft(text: String) {
        waitFor("composer") { editor() != null }
        check(editor()!!.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }))
    }

    override fun onStart() {
        var output: File? = null
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(
                targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
                flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            }
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            output = File(targetContext.getExternalFilesDir(null), "profile-switch-qa").apply { mkdirs() }
            val sid = checkNotNull(fixture.getString("session"))
            val client = BridgeClient(checkNotNull(fixture.getString("url")), checkNotNull(fixture.getString("token")),
                checkNotNull(fixture.getString("fingerprint")))
            val initial = client.sessions()
            @Suppress("UNCHECKED_CAST")
            val sessions = Bridge.javaClass.getDeclaredField("_sessions").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<List<SessionSummary>>
            runOnMainSync {
                Bridge.setLanguage(AppLanguage.English)
                Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, client)
                sessions.value = initial
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    SessionScreen(sid, onMenu = {}, onBack = {}, onOpenSession = {})
                } } }
            }
            waitFor("original chat history") { find("Retained fixture response") != null }
            setDraft("Retained draft")
            tap("Choose executor profile")
            waitFor("verified local model") { find("local-fixture-model") != null }
            uiAutomation.takeScreenshot()?.let { image -> File(output, "profile-menu.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            tap("local-fixture-model")
            waitFor("same chat switched to local") {
                Bridge.sessionDetail.value?.meta?.get("profile")?.jsonPrimitive?.content == "local" &&
                    !Bridge.profileSwitching.value && find("Selected") == null
            }
            check(Bridge.sessionDetail.value?.sessionId == sid)
            check(editor()?.text?.toString() == "Retained draft")
            check(find("Retained fixture response") != null)
            check(find("local-fixture-model") != null)
            tap("Choose executor profile")
            tap("cloud-fixture-model")
            waitFor("switch back to cloud") { Bridge.sessionDetail.value?.meta?.get("profile")?.jsonPrimitive?.content == "cloud" && !Bridge.profileSwitching.value && find("Selected") == null }
            tap("Choose executor profile")
            tap("local-fixture-model")
            waitFor("second local selection") { Bridge.sessionDetail.value?.meta?.get("profile")?.jsonPrimitive?.content == "local" && !Bridge.profileSwitching.value && find("Selected") == null }
            check(editor()?.text?.toString() == "Retained draft")
            uiAutomation.takeScreenshot()?.let { image -> File(output, "same-chat-local.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            tap("Send")
            waitFor("new task accepted") { sessions.value.any { it.sessionId == sid && it.active } }
            setDraft("Keep busy draft")
            tap("Choose executor profile")
            waitFor("busy explanation") { find("Wait for the current task or send to finish") != null }
            var node = find("cloud-fixture-model")
            while (node != null && !node.isClickable) node = node.parent
            check(node != null && !node.isEnabled)
            val rejected = runCatching { client.switchSessionProfile(sid, "cloud") }.exceptionOrNull()
            check(rejected is BridgeException && rejected.status == 409)
            check(client.session(sid).meta["profile"]?.jsonPrimitive?.content == "local")
            uiAutomation.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
            waitFor("menu dismissed") { find("Selected") == null }
            check(editor()?.text?.toString() == "Keep busy draft")
            sendStatus(0, Bundle().apply { putString("stream", "PROFILE_SWITCH_PASS: verified menu, same chat/history/draft, cloud/local/back, next task, busy guard and server rejection\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            if (output != null) uiAutomation.takeScreenshot()?.let { image -> File(output, "failure.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        }
    }
}
