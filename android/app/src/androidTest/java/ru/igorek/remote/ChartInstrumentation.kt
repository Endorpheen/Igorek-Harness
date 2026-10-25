package ru.igorek.remote

import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.*
import ru.igorek.remote.ui.*
import java.io.File

class ChartInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }
    override fun onStart() {
        try {
            val activity = startActivitySync(Intent(targetContext, UiAcceptanceActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            val input = """{"type":"bar","title":"Агентные тесты","labels":["Terminal-Bench 2.1","DeepSWE","Automation Bench"],"series":[{"name":"Nanbeige4.3","values":[80.9,65.5,54.2]},{"name":"Qwen3.8","values":[73,42.5,38.3]}],"unit":"баллы","source":"Демонстрация интерфейса · значения из примера","y_min":0,"y_max":100}"""
            val item = TimelineItem.ToolCall("fixture-chart", "render_chart", input, "done", """{"ok":true,"kind":"igorek.chart","schema_version":1,"chart_id":"fixture"}""")
            check(chartResult(item) != null)
            runOnMainSync {
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
                activity.setContent { CompositionLocalProvider(LocalAppLanguage provides AppLanguage.Russian) { IgorekTheme {
                    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(12.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                        Text("Игорёк", style = MaterialTheme.typography.titleLarge)
                        TimelineRow(TimelineItem.AssistantText("fixture-intro", "Сравнение результатов. Точные значения показаны рядом с каждой моделью."))
                        TimelineRow(item)
                    }
                } } }
            }
            waitForIdleSync()
            fun visible(node: android.view.accessibility.AccessibilityNodeInfo?, label: String = "Агентные тесты"): Boolean {
                if (node == null) return false
                if (node.isVisibleToUser && node.text?.toString() == label) return true
                return (0 until node.childCount).any { visible(node.getChild(it), label) }
            }
            val deadline = SystemClock.elapsedRealtime() + 90000
            while (!visible(uiAutomation.rootInActiveWindow) && SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(250)
            check(visible(uiAutomation.rootInActiveWindow)) { "Chart did not become visible" }
            SystemClock.sleep(800)
            val output = File(targetContext.cacheDir,"chart-qa").apply { mkdirs() }
            var image = uiAutomation.takeScreenshot()
            while (image == null && SystemClock.elapsedRealtime() < deadline) { SystemClock.sleep(250); image = uiAutomation.takeScreenshot() }
            val captured = image ?: error("No native screenshot")
            File(output,"android-chart.png").outputStream().use { captured.compress(Bitmap.CompressFormat.PNG,100,it) };captured.recycle()
            File(output,"result.txt").writeText("CHART_NATIVE_PASS")
            runOnMainSync {
                activity.setContent { IgorekTheme { TimelineRow(item.copy(status = "error", resultPreview = """{"error":"Добавьте подписи категорий"}""")) } }
            }
            val errorDeadline = SystemClock.elapsedRealtime() + 15000
            while (!visible(uiAutomation.rootInActiveWindow, "Добавьте подписи категорий") && SystemClock.elapsedRealtime() < errorDeadline) SystemClock.sleep(100)
            check(visible(uiAutomation.rootInActiveWindow, "Добавьте подписи категорий")) { "Chart validation error was hidden" }
            println("CHART_NATIVE_PASS")
            runOnMainSync { activity.finish() }
            finish(0,Bundle())
        } catch (error: Throwable) { error.printStackTrace();finish(1,Bundle().apply { putString("error",error.toString()) }) }
    }
}
