package ru.igorek.remote

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.ProfileScreen
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONObject

class SchedulesInstrumentation : Instrumentation() {
    private var base = "http://127.0.0.1:48661"
    private var pairCode = ""
    private var tag = "phone"
    @Volatile private var opened = ""
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
    private fun actionable(label: String): Boolean {
        var node: AccessibilityNodeInfo? = find(label) ?: return false
        while (node != null) {
            if (!node.isEnabled) return false
            if (node.isClickable) return true
            node = node.parent
        }
        return false
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
    private fun setField(index: Int, text: String) {
        val input = nodes().filter { it.isVisibleToUser && it.isEditable }[index]
        input.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        check(input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }))
        Thread.sleep(150)
    }
    private fun setValue(old: String, value: String) {
        for (i in 0..8) {
            val input = nodes().firstOrNull { it.isVisibleToUser && it.isEditable && it.text?.toString() == old }
            if (input != null) {
                check(input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
                }))
                Thread.sleep(200)
                return
            }
            nodes().firstOrNull { it.isVisibleToUser && it.isScrollable }?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
            Thread.sleep(300)
        }
        error("Missing editable value $old")
    }
    private fun qa(path: String = "/qa"): JSONObject {
        val connection = URL(base + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 3000; connection.readTimeout = 5000
        try { return connection.inputStream.bufferedReader().use { JSONObject(it.readText()) } }
        finally { connection.disconnect() }
    }
    private fun screenshot(name: String) {
        val folder = File(targetContext.getExternalFilesDir(null), "schedules-qa").apply { mkdirs() }
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
            runOnMainSync { Bridge.pair(base, pairCode, "Schedule QA", "") { failure = it; paired.countDown() } }
            check(paired.await(15, TimeUnit.SECONDS) && failure == null) { "Local fixture pairing failed" }
            val activity = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            runOnMainSync { activity.setContent { IgorekTheme() { ProfileScreen({}, { opened = it }) } } }
            waitFor("schedule list") { find("Утренняя проверка локального проекта") != null }
            check(find("Игорь") != null)
            screenshot("01-list")
            tap("Действия: Утренняя проверка локального проекта")
            screenshot("02-menu")
            tap("Приостановить")
            waitFor("paused schedule") { find("На паузе") != null }
            tap("Действия: Утренняя проверка локального проекта"); tap("Возобновить")
            waitFor("resumed schedule") { find("Запланировано") != null }
            tap("Действия: Утренняя проверка локального проекта"); tap("Изменить")
            waitFor("editor options") { find("Обновить профили") != null && find("Профиль: schedule-demo") != null }
            setField(0, "Утренний обзор")
            tap("Сохранить задание")
            waitFor("updated name") { find("Утренний обзор") != null && find("Изменить задание") == null }
            tap("Действия: Утренний обзор"); tap("Выполнить сейчас")
            waitFor("queued test run") { find("В очереди") != null }
            tap("Действия: Утренний обзор"); check(!actionable("Выполнить сейчас")); tap("Открыть последний результат")
            waitFor("result navigation") { opened.isNotBlank() }
            val row = qa().getJSONObject("schedules").getJSONArray("schedules").let { array ->
                (0 until array.length()).map { array.getJSONObject(it) }.first { it.getString("name") == "Утренний обзор" }
            }
            check(opened == row.getString("last_session_id"))
            tap("Действия: Утренний обзор"); tap("Удалить")
            screenshot("03-delete")
            tap("Удалить")
            waitFor("deleted schedule") { find("Утренний обзор") == null }
            progress("CRUD: pause/resume/edit/manual queue/result/delete through real schedule API")
            tap("Создать задание")
            waitFor("new editor") { find("Обновить профили") != null && find("Профиль: schedule-demo") != null }
            tap("Название")
            setField(0, "Проверка с телефона")
            setField(1, "Это тест без выполнения модели.")
            screenshot("04-editor")
            tap("09:00 · Europe/Moscow")
            screenshot("05-time-picker")
            val cancel = nodes().firstOrNull { it.viewIdResourceName == "android:id/button2" }
            check(cancel?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true)
            tap("Папка и часовой пояс")
            setValue("Europe/Moscow", "Not/AZone")
            tap("Сохранить задание")
            waitFor("server validation error") { find("Неизвестный часовой пояс") != null }
            screenshot("06-validation")
            setValue("Not/AZone", "Europe/Moscow")
            tap("Сохранить задание")
            waitFor("created schedule") { find("Проверка с телефона") != null && find("Новое задание") == null }
            tap("Еженедельный отчёт")
            locate("Этот редактор поддерживает ежедневные и разовые задания.")
            screenshot("07-cron-details")
            tap("Закрыть")
            qa("/qa/mode?available=0")
            tap("Обновить расписания")
            waitFor("unsupported connection") { find("Расписания доступны при подключении") != null }
            val addBounds = android.graphics.Rect()
            locate("Создать задание").getBoundsInScreen(addBounds)
            val pressedAt = SystemClock.uptimeMillis()
            for (action in listOf(android.view.MotionEvent.ACTION_DOWN, android.view.MotionEvent.ACTION_UP)) {
                val event = android.view.MotionEvent.obtain(pressedAt, SystemClock.uptimeMillis(), action, addBounds.exactCenterX(), addBounds.exactCenterY(), 0)
                uiAutomation.injectInputEvent(event, true); event.recycle()
            }
            Thread.sleep(400)
            check(find("Новое задание") == null) { "Unavailable server allowed task editing" }
            screenshot("08-unavailable")
            qa("/qa/mode?available=1"); tap("Обновить расписания")
            waitFor("restored server") { find("Проверка с телефона") != null }
            screenshot("09-final")
            val ops = qa().getJSONArray("operations")
            val manual = (0 until ops.length()).map { ops.getJSONObject(it) }.filter { it.getString("path").endsWith("/run") }
            check(manual.size == 1)
            progress("CREATE: time picker, remote folder/timezone, validation without losing form; Cron read-only; unavailable server")
            finish(Activity.RESULT_OK, Bundle().apply { putString("stream", "\nSCHEDULES_PROFILE_PASS $tag\n") })
        } catch (failure: Throwable) {
            screenshot("failure")
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("stream", "\nSCHEDULES_PROFILE_FAIL $tag: ${failure.stackTraceToString()}\n") })
        }
    }
}
