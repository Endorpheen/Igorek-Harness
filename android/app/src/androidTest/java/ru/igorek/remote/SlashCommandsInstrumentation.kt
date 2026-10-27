package ru.igorek.remote

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.AdaptiveIconDrawable
import android.graphics.Rect
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.BridgeClient
import ru.igorek.remote.data.SessionDetail
import ru.igorek.remote.data.SessionGoal
import ru.igorek.remote.data.ExtensionsResponse
import ru.igorek.remote.data.Prefs
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.data.matchingSlashCommands
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.SessionScreen
import ru.igorek.remote.ui.SlashCommandMenu
import java.io.File

class SlashCommandsInstrumentation : Instrumentation() {
    private var fixture = Bundle()
    override fun onCreate(arguments: Bundle?) { fixture = arguments ?: Bundle(); super.onCreate(arguments); start() }

    private fun node(predicate: (AccessibilityNodeInfo) -> Boolean): AccessibilityNodeInfo? {
        fun visit(value: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (value == null) return null
            value.refresh()
            if (value.isVisibleToUser && predicate(value)) return value
            for (i in 0 until value.childCount) visit(value.getChild(i))?.let { return it }
            return null
        }
        return visit(uiAutomation.rootInActiveWindow)
    }
    private fun editor() = node { it.isEditable }
    private fun label(text: String) = node { it.text?.toString() == text }
    private fun planButton(): AccessibilityNodeInfo? {
        var current = node { it.text?.toString() == "Plan" || it.contentDescription?.toString() == "Plan" }
        while (current != null) {
            current.refresh()
            if (current.isCheckable && current.isClickable) return current
            current = current.parent
        }
        return null
    }
    private fun waitFor(name: String, predicate: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 15000
        while (SystemClock.elapsedRealtime() < until) { if (predicate()) return; Thread.sleep(100) }
        error("Timed out: $name")
    }
    private fun tap(value: AccessibilityNodeInfo) {
        val box = Rect(); value.getBoundsInScreen(box)
        val time = SystemClock.uptimeMillis()
        for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
            val event = MotionEvent.obtain(time, SystemClock.uptimeMillis(), action, box.centerX().toFloat(), box.centerY().toFloat(), 0)
            check(uiAutomation.injectInputEvent(event, true)); event.recycle(); Thread.sleep(80)
        }
    }
    private fun draft(text: String) {
        check(editor()!!.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }))
        waitFor("draft $text") { editor()?.text?.toString() == text }
    }
    private fun screenshot(name: String) {
        uiAutomation.takeScreenshot()?.let { bitmap ->
            File(targetContext.getExternalFilesDir(null), "slash-$name.png").outputStream().use {
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
            }; bitmap.recycle()
        }
    }

    override fun onStart() {
        try {
            uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
                flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            }
            val activity = startActivitySync(Intent(targetContext, UiAcceptanceActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            val icon = targetContext.packageManager.getApplicationIcon(targetContext.packageName)
            check(icon is AdaptiveIconDrawable)
            val iconBitmap = Bitmap.createBitmap(216, 216, Bitmap.Config.ARGB_8888)
            icon.setBounds(0, 0, 216, 216)
            icon.draw(Canvas(iconBitmap))
            val pixels = IntArray(216 * 216)
            iconBitmap.getPixels(pixels, 0, 216, 0, 0, 216, 216)
            check(pixels.count { pixel -> (pixel ushr 24) > 128 && (pixel and 255) > ((pixel ushr 16) and 255) + 30 } > 1000)
            File(targetContext.getExternalFilesDir(null), "launcher-icon.png").outputStream().use {
                iconBitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
            }
            iconBitmap.recycle()
            @Suppress("UNCHECKED_CAST")
            val detail = Bridge.javaClass.getDeclaredField("_sessionDetail").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<SessionDetail?>
            @Suppress("UNCHECKED_CAST")
            val extensions = Bridge.javaClass.getDeclaredField("_extensions").apply { isAccessible = true }
                .get(Bridge) as MutableStateFlow<ExtensionsResponse>
            val sessionId = "slash-fixture"
            val verifyPersistence = fixture.getString("verify_persistence") == "true"
            runOnMainSync {
                Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, null)
                Bridge.setLanguage(AppLanguage.English)
                if (!verifyPersistence) Bridge.setPlanModeEnabled(sessionId, false)
                extensions.value = ExtensionsResponse(profile = "fixture-profile", backend = "cc-backend")
                detail.value = SessionDetail(sessionId = sessionId, meta = buildJsonObject {
                    put("profile", "fixture-profile"); put("cwd", "/workspace")
                })
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    SessionScreen(sessionId, onMenu = {}, onBack = {}, onOpenSession = {})
                } } }
            }
            waitFor("composer placeholder") { label("Message or /command") != null && editor() != null }
            if (verifyPersistence) {
                waitFor("plan after process restart") { planButton()?.isChecked == true }
                tap(planButton()!!)
                waitFor("plan disabled after restart") { planButton()?.isChecked == false }
                check(!Prefs(targetContext).planModeEnabled(sessionId))
                sendStatus(0, Bundle().apply { putString("stream", "PLAN_RESTART_PASS: persisted after process restart and disabled by user\n") })
                finish(Activity.RESULT_OK, Bundle())
                return
            }
            tap(editor()!!)
            fun submitCommand(command: String) {
                tap(editor()!!)
                draft(command)
                waitFor("keyboard") { uiAutomation.windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD } }
                uiAutomation.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
                waitFor("keyboard closed") { uiAutomation.windows.none { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD } }
                waitFor("send button") { node { it.contentDescription?.toString() == "Send" && it.isEnabled } != null }
                tap(node { it.contentDescription?.toString() == "Send" && it.isEnabled }!!)
            }
            submitCommand("/help")
            waitFor("help dialog") { label("Chat commands") != null }
            check(label("/fast") == null && label("/ultrafast") == null)
            tap(label("Close")!!)
            submitCommand("/status")
            waitFor("status dialog") { label("Status") != null && label("slash-fixture") != null }
            check(label("/workspace") != null)
            tap(label("Close")!!)
            waitFor("plan button") { planButton() != null }
            check(planButton()?.isChecked == false)
            tap(planButton()!!)
            waitFor("plan on") { planButton()?.isChecked == true }
            check(Prefs(targetContext).planModeEnabled(sessionId))
            check(!Prefs(targetContext).planModeEnabled("another-session"))
            screenshot("plan-on")
            val fixtureClient = fixture.getString("url")?.let { url ->
                BridgeClient(url, "fixture-plan-token", checkNotNull(fixture.getString("fingerprint")))
            }
            fixtureClient?.let { client ->
                runOnMainSync {
                    Bridge.javaClass.getDeclaredField("selectedSessionId").apply { isAccessible = true }.set(Bridge, sessionId)
                    Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, client)
                }
                for (message in listOf("First planning request", "Second planning request")) {
                    submitCommand(message)
                    try { waitFor("sent $message") { Bridge.timeline.value.any { it is TimelineItem.UserText && it.text == message } } }
                    catch (error: Throwable) {
                        val diagnostic = Bridge.notifications.value.lastOrNull { it.diagnostic != null }?.diagnostic
                        sendStatus(0, Bundle().apply { putString("stream", "SEND_DIAGNOSTIC: error=${Bridge.lastError.value} code=${diagnostic?.code} native=${diagnostic?.nativeCode} phase=${diagnostic?.phase} delivery=${diagnostic?.delivery} profile=${detail.value?.meta?.get("profile")} backend=${extensions.value.backend} draft=${editor()?.text}\n") })
                        throw error
                    }
                    check(planButton()?.isChecked == true)
                    check(Prefs(targetContext).planModeEnabled(sessionId))
                }
                runOnMainSync {
                    detail.value = SessionDetail(sessionId = sessionId, meta = buildJsonObject { put("profile", "unsupported") })
                    extensions.value = ExtensionsResponse(profile = "unsupported", backend = "codex")
                }
                waitFor("unsupported profile retains plan") { planButton()?.isChecked == true }
                submitCommand("Blocked unsafe request")
                waitFor("blocked draft retained") { editor()?.text?.toString() == "Blocked unsafe request" }
                check(planButton()?.isChecked == true)
                runOnMainSync {
                    detail.value = SessionDetail(sessionId = sessionId, meta = buildJsonObject {
                        put("profile", "fixture-profile"); put("cwd", "/workspace")
                    })
                    extensions.value = ExtensionsResponse(profile = "fixture-profile", backend = "cc-backend")
                }
                runOnMainSync { Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, null) }
            }
            runOnMainSync { activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                SessionScreen(sessionId, onMenu = {}, onBack = {}, onOpenSession = {})
            } } } }
            waitFor("plan restored") { planButton()?.isChecked == true }
            tap(planButton()!!)
            try { waitFor("plan off") { planButton()?.isChecked == false } }
            catch (error: Throwable) {
                val value = planButton()
                sendStatus(0, Bundle().apply { putString("stream", "PLAN_OFF_DIAGNOSTIC: button=$value saved=${Prefs(targetContext).planModeEnabled(sessionId)}\n") })
                throw error
            }
            check(!Prefs(targetContext).planModeEnabled(sessionId))
            screenshot("plan-off")
            fixtureClient?.let { client ->
                runOnMainSync { Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, client) }
                submitCommand("Ordinary request after disabling plan")
                waitFor("ordinary request sent") { Bridge.timeline.value.any { it is TimelineItem.UserText && it.text == "Ordinary request after disabling plan" } }
                check(planButton()?.isChecked == false)
                runOnMainSync { Bridge.javaClass.getDeclaredField("client").apply { isAccessible = true }.set(Bridge, null) }
            }
            tap(editor()!!)
            draft("/go")
            waitFor("goal suggestion") { label("/goal") != null }
            check(label("/goal pause") == null)
            screenshot("create")
            tap(label("/goal")!!)
            waitFor("completion cursor") {
                editor()?.let { it.text?.toString() == "/goal " && it.textSelectionStart == 6 && it.textSelectionEnd == 6 } == true
            }
            sendStringSync("Build feature")
            waitFor("objective typed after command") { editor()?.text?.toString() == "/goal Build feature" }
            screenshot("highlight")
            check(label("/goal") == null)
            check(detail.value?.goal == null)
            val active = SessionGoal(goalId = "fixture-goal", objective = "Keep this objective", status = "active")
            runOnMainSync { detail.value = SessionDetail(sessionId = sessionId, goal = active) }
            draft("/goal ")
            waitFor("active controls") { label("/goal pause") != null }
            check(label("/goal resume") == null)
            screenshot("active")
            tap(label("/goal pause")!!)
            waitFor("pause completion") { editor()?.text?.toString() == "/goal pause " }
            check(detail.value?.goal == active)
            draft("/goal e")
            waitFor("filtered edit") { label("/goal edit") != null && label("/goal pause") == null }
            tap(label("/goal edit")!!)
            waitFor("edit completion") { editor()?.text?.toString() == "/goal edit " }
            check(detail.value?.goal == active)
            sendStringSync("Revised objective")
            waitFor("edit cursor") { editor()?.text?.toString() == "/goal edit Revised objective" }
            runOnMainSync { detail.value = SessionDetail(sessionId = sessionId, goal = active.copy(status = "paused")) }
            draft("/goal r")
            waitFor("resume controls") { label("/goal resume") != null }
            check(label("/goal pause") == null)
            tap(label("/goal resume")!!)
            waitFor("resume completion") { editor()?.text?.toString() == "/goal resume " }
            check(detail.value?.goal?.status == "paused")
            draft("/goal c")
            waitFor("clear suggestion") { label("/goal clear") != null }
            tap(label("/goal clear")!!)
            waitFor("clear completion") { editor()?.text?.toString() == "/goal clear " }
            check(detail.value?.goal?.goalId == active.goalId)
            draft("Ordinary text /goal")
            check(label("/goal resume") == null)
            draft("\$unknown")
            check(label("/goal") == null)
            draft("/unknown")
            check(label("/goal") == null)
            draft("Hello world")
            check(editor()!!.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 5)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, 5)
            }))
            runOnMainSync { detail.value = SessionDetail(sessionId = sessionId, goal = active.copy(status = "complete")) }
            waitFor("retained cursor after goal refresh") { editor()?.textSelectionStart == 5 }
            sendStringSync(" brave")
            waitFor("ordinary middle editing") { editor()?.text?.toString() == "Hello brave world" }
            draft("/goal\nKeep multiline objective")
            check(label("/goal pause") == null && editor()?.text?.toString() == "/goal\nKeep multiline objective")
            var selected = false
            runOnMainSync { activity.setContent { IgorekTheme {
                SlashCommandMenu(matchingSlashCommands("/goal ", active), enabled = false) { selected = true }
            } } }
            waitFor("disabled suggestion") { label("/goal pause") != null }
            tap(label("/goal pause")!!)
            waitForIdleSync()
            check(!selected)
            if (fixture.getString("verify_on_restart") == "true") {
                runOnMainSync { activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    SessionScreen(sessionId, onMenu = {}, onBack = {}, onOpenSession = {})
                } } } }
                waitFor("plan button before restart") { planButton() != null }
                tap(planButton()!!)
                waitFor("plan saved before restart") { planButton()?.isChecked == true }
                check(Prefs(targetContext).planModeEnabled(sessionId))
            }
            sendStatus(0, Bundle().apply { putString("stream", "SLASH_COMMANDS_PASS: actual SessionScreen, prefix filtering, state controls, tap completion, cursor typing, preserved goal and ordinary drafts\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            screenshot("failure")
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", error.stackTraceToString()) })
        }
    }
}
