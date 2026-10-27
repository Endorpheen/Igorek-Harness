package ru.igorek.remote

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.DropdownMenu
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.ProfileChoice
import ru.igorek.remote.data.SessionOptionsResponse
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.LocalAppLanguage
import ru.igorek.remote.ui.SessionProfileMenuContent
import java.io.File

class ProfileMenuInstrumentation : Instrumentation() {
    private fun node(text: String): AccessibilityNodeInfo? {
        fun visit(value: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (value == null) return null
            value.refresh()
            if (value.isVisibleToUser && value.text?.toString() == text) return value
            for (index in 0 until value.childCount) visit(value.getChild(index))?.let { return it }
            return null
        }
        return uiAutomation.windows.firstNotNullOfOrNull { visit(it.root) }
    }
    private fun waitFor(text: String, present: Boolean = true) {
        val deadline = SystemClock.elapsedRealtime() + 15000
        while (SystemClock.elapsedRealtime() < deadline) {
            if ((node(text) != null) == present) return
            Thread.sleep(100)
        }
        error("Unexpected visibility: $text")
    }
    private fun click(text: String) {
        var current = node(text)
        while (current != null && !current.isClickable) current = current.parent
        check(current?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) { "Cannot click $text" }
    }
    private fun scrollTo(text: String) {
        fun scrollable(value: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (value == null) return null
            if (value.isVisibleToUser && value.isScrollable) return value
            for (index in 0 until value.childCount) scrollable(value.getChild(index))?.let { return it }
            return null
        }
        repeat(20) {
            if (node(text) != null) return
            val scroll = uiAutomation.windows.firstNotNullOfOrNull { scrollable(it.root) }
            check(scroll?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD) == true) { "Cannot scroll to $text" }
            Thread.sleep(100)
        }
        waitFor(text)
    }
    private fun screenshot(name: String) {
        Thread.sleep(250)
        uiAutomation.takeScreenshot()?.let { bitmap ->
            File(targetContext.getExternalFilesDir(null), "profile-menu-$name.png").outputStream().use {
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
            }
            bitmap.recycle()
        }
    }
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }
    override fun onStart() {
        try {
            uiAutomation.serviceInfo = uiAutomation.serviceInfo.apply {
                flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            }
            val activity = startActivitySync(Intent(targetContext, UiAcceptanceActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            val current = ProfileChoice(name = "cloud", model = "default-model", models = listOf("model-a", "model-b") + (1..35).map { "go-model-$it" },
                modelLabels = mapOf("model-a" to "Model A", "model-b" to "Model B"), providerIcon = "openai")
            val others = (1..12).map { ProfileChoice(name = "local-$it", model = "fixture", providerIcon = "local") }
            val options = SessionOptionsResponse(profileCatalog = listOf(current) + others, profileSwitchSupported = true)
            var selected = ""
            for (name in listOf("anthropic", "custom", "deepseek", "google", "inception", "local", "minimax",
                "moonshotai", "openai", "opencode", "openrouter", "xai", "zai")) {
                val id = targetContext.resources.getIdentifier("provider_$name", "drawable", targetContext.packageName)
                check(id != 0) { "Missing provider icon: $name" }
                val bitmap = BitmapFactory.decodeResource(targetContext.resources, id)
                val pixels = IntArray(bitmap.width * bitmap.height)
                bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                check(pixels.any { (it ushr 24) != 0 }) { "Empty provider icon: $name" }
                bitmap.recycle()
            }
            runOnMainSync {
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.English) { IgorekTheme {
                    var showProfiles by remember { mutableStateOf(false) }
                    Box { DropdownMenu(expanded = true, onDismissRequest = {}, modifier = Modifier.widthIn(min = 240.dp, max = 320.dp).heightIn(max = 480.dp)) {
                        SessionProfileMenuContent(options, "cloud", "model-a", false, false, false, null, showProfiles,
                            onToggleProfiles = { showProfiles = !showProfiles }, onProfile = { selected = it },
                            onModel = { selected = it }, onRetry = {})
                    } }
                } } }
            }
            waitFor("cloud"); waitFor("Model A"); waitFor("Other profiles · 12"); waitFor("local-1", false)
            screenshot("compact")
            click("Other profiles · 12"); waitFor("local-1")
            screenshot("expanded")
            click("local-1"); check(selected == "local-1")
            click("Other profiles · 12"); waitFor("local-1", false); waitFor("Model B")
            click("Model B"); check(selected == "model-b")
            scrollTo("go-model-35"); click("go-model-35"); check(selected == "go-model-35")
            sendStatus(0, Bundle().apply { putString("stream", "PROFILE_MENU_PASS: compact menu, expandable profiles, model and profile selection\n") })
            finish(Activity.RESULT_OK, Bundle())
        } catch (error: Throwable) {
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", "PROFILE_MENU_FAILURE: $error\n") })
        }
    }
}
