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
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeClient
import ru.igorek.remote.data.Prefs
import java.io.File

class RemoteUnlockInstrumentation : Instrumentation() {
    private var arguments = Bundle()
    override fun onCreate(arguments: Bundle?) { this.arguments = arguments ?: Bundle(); super.onCreate(arguments); start() }

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

    private fun until(label: String, condition: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 60000
        while (SystemClock.elapsedRealtime() < deadline) { if (condition()) return; Thread.sleep(150) }
        error("Timed out: $label")
    }

    private fun submit(code: String) {
        until("enabled unlock input") {
            editor()?.takeIf { it.isEnabled }?.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, code)
            }) == true
        }
        until("enabled unlock button") {
            find("Unlock")?.isEnabled == true
        }
        val box = Rect(); find("Unlock")!!.getBoundsInScreen(box)
        val time = SystemClock.uptimeMillis()
        for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
            val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action,
                box.centerX().toFloat(), box.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(event, true)); event.recycle(); Thread.sleep(80)
        }
    }

    override fun onStart() {
        var activity: ComponentActivity? = null
        try {
            uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
                flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            }
            if (android.os.Build.VERSION.SDK_INT >= 33) uiAutomation.grantRuntimePermission(
                targetContext.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
            val url = checkNotNull(arguments.getString("url"))
            val token = checkNotNull(arguments.getString("token"))
            val fingerprint = checkNotNull(arguments.getString("fingerprint"))
            val prefs = Prefs(targetContext)
            prefs.baseUrl = url; prefs.token = token; prefs.fingerprint = fingerprint
            prefs.deviceName = "Acceptance phone"; prefs.appLanguage = "en"; prefs.lastSeq = 0
            prefs.saveCurrentConnection("Remote unlock acceptance")
            val screen = startActivitySync(Intent(targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            activity = screen
            runOnMainSync { screen.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
            until("locked startup screen") { find("Vault locked") != null && Bridge.connection.value == "online" }
            check(Bridge.paired.value)
            submit("incorrect-fixture-code")
            until("incorrect code stays on unlock screen") { find("Incorrect code. Try again.") != null }
            check(Bridge.paired.value && prefs.token == token && prefs.fingerprint == fingerprint)
            submit("fixture-unlock-code")
            until("normal session list after unlock") { find("Retained unlock fixture") != null }
            val client = BridgeClient(url, token, fingerprint)
            check(!client.vaultStatus().requiresUnlock)
            val output = File(targetContext.getExternalFilesDir(null), "remote-unlock-qa").apply { mkdirs() }
            uiAutomation.takeScreenshot()?.let { image -> File(output, "unlocked.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            val http = client.javaClass.getDeclaredField("http").apply { isAccessible = true }.get(client) as OkHttpClient
            val request = Request.Builder().url(checkNotNull(arguments.getString("control_url")) + "/restart")
                .header("Authorization", "Bearer $token").post("{}".toRequestBody()).build()
            http.newCall(request).execute().use { check(it.isSuccessful) }
            until("same phone reconnects to restarted desktop") { find("Vault locked") != null && Bridge.connection.value == "online" }
            check(Bridge.paired.value && prefs.token == token && prefs.fingerprint == fingerprint)
            uiAutomation.takeScreenshot()?.let { image -> File(output, "locked-after-restart.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            submit("fixture-unlock-code")
            until("same saved session after second unlock") { find("Retained unlock fixture") != null }
            check(!client.vaultStatus().requiresUnlock)
            check(Bridge.paired.value && prefs.token == token && prefs.fingerprint == fingerprint)
            File(output, "result.json").writeText("""{"passed":true,"actual_desktop_restart":true,"pairing_retained":true,"wrong_code_retained_pairing":true,"unlock_count":2}""")
            finish(Activity.RESULT_OK, Bundle().apply { putString("result", "REMOTE_UNLOCK_PASS: restart, existing pairing, wrong and correct code, retained session") })
        } catch (error: Throwable) {
            val output = File(targetContext.getExternalFilesDir(null), "remote-unlock-qa").apply { mkdirs() }
            uiAutomation.takeScreenshot()?.let { image -> File(output, "failure.png").outputStream().use {
                image.compress(Bitmap.CompressFormat.PNG, 100, it) }; image.recycle() }
            finish(Activity.RESULT_CANCELED, Bundle().apply {
                putString("error", error.stackTraceToString())
                putString("state", "${Bridge.vaultAccess.value?.state}/${Bridge.connection.value}/busy=${Bridge.vaultUnlockBusy.value}/error=${Bridge.vaultUnlockError.value}")
                putString("diagnostic", Bridge.notifications.value.lastOrNull()?.diagnostic?.let {
                    "${it.code}/${it.nativeCode}/${it.httpStatus}"
                })
            })
        } finally {
            runOnMainSync { Bridge.stopStream(); activity?.finish() }
        }
    }
}
