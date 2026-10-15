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
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.ui.IgorekTheme
import ru.igorek.remote.ui.SessionScreen
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking

class ScheduledDeliveryInstrumentation : Instrumentation() {
    private var base = ""
    private var code = ""
    private var fingerprint = ""
    private val source = "chat-enews-qa"
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        base = arguments?.getString("base_url").orEmpty()
        code = arguments?.getString("pair_code").orEmpty()
        fingerprint = arguments?.getString("fingerprint").orEmpty()
        start()
    }
    private fun waitFor(label: String, predicate: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + 25000
        while(SystemClock.elapsedRealtime() < until) {
            if(predicate())return
            Thread.sleep(150)
        }
        error("Timed out: $label")
    }
    private fun texts(): List<String> {
        val result=mutableListOf<String>()
        fun visit(node: AccessibilityNodeInfo?) {
            if(node==null)return
            node.refresh()
            if(node.isVisibleToUser)node.text?.toString()?.let(result::add)
            for(i in 0 until node.childCount)visit(node.getChild(i))
        }
        visit(uiAutomation.rootInActiveWindow)
        return result
    }
    override fun onStart() {
        try {
            if(android.os.Build.VERSION.SDK_INT>=33)uiAutomation.grantRuntimePermission(targetContext.packageName,android.Manifest.permission.POST_NOTIFICATIONS)
            runOnMainSync { Bridge.init(targetContext) }
            val paired=CountDownLatch(1);var failure:Throwable?=null
            runOnMainSync { Bridge.pair(base,code,"Delivery QA",fingerprint){failure=it;paired.countDown()} }
            check(paired.await(15,TimeUnit.SECONDS)&&failure==null){"Fixture pairing failed: $failure"}
            val activity=startActivitySync(Intent(targetContext,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as ComponentActivity
            fun show(id:String) {runOnMainSync {activity.setContent {IgorekTheme {SessionScreen(sessionId=id,onMenu={},onBack={},onOpenSession={})}}}}
            show(source)
            waitFor("original source question") {texts().any{it.contains("Правила исходного треда")}}
            waitFor("live scheduled text") {texts().any{it.contains("Фоновый результат eNews")}}
            waitFor("inline downloadable attachment") {texts().any{it.contains("post-123.txt")}}
            val row=Bridge.timeline.value.filterIsInstance<TimelineItem.AssistantText>().single{it.text=="Фоновый результат eNews"}
            check(row.files.single().name=="post-123.txt")
            val output=ByteArrayOutputStream()
            runBlocking {Bridge.download(source,row.files.single(),output){}}
            check(output.toString("UTF-8")=="attachment fixture")
            show("chat-empty-qa")
            waitFor("another thread") {Bridge.sessionDetail.value?.sessionId=="chat-empty-qa"}
            check(Bridge.timeline.value.none{it is TimelineItem.AssistantText&&it.text=="Фоновый результат eNews"})
            show(source)
            waitFor("restored source result") {texts().any{it.contains("Фоновый результат eNews")}}
            check(Bridge.timeline.value.filterIsInstance<TimelineItem.AssistantText>().count{it.text=="Фоновый результат eNews"}==1)
            val image=uiAutomation.takeScreenshot()?:error("Screenshot unavailable")
            val folder=targetContext.getExternalFilesDir("delivery-qa")!!;folder.mkdirs()
            File(folder,"scheduled-result.png").outputStream().use{image.compress(Bitmap.CompressFormat.PNG,100,it)};image.recycle()
            sendStatus(0,Bundle().apply{putString("stream","\nMOBILE_SCHEDULE_DELIVERY_PASS: source-thread live/history, no duplicate, inline file, authenticated bytes\n")})
            finish(Activity.RESULT_OK,Bundle())
        }catch(error:Throwable){finish(Activity.RESULT_CANCELED,Bundle().apply{putString("stream",error.stackTraceToString())})}
    }
}
