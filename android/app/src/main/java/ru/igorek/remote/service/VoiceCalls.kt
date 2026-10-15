package ru.igorek.remote.service

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import ru.igorek.remote.data.AppLocale

internal object VoiceCalls {
    private val mutable = MutableStateFlow(VoiceCallState())
    val state = mutable.asStateFlow()
    var service: VoiceCallService? = null
    var activityVisible = false
        set(value) { field = value; if (!value) service?.cancelPushToTalk(); service?.refreshOverlay() }

    fun update(transform: (VoiceCallState) -> VoiceCallState) { mutable.value = transform(mutable.value) }

    fun open(context: Context, sessionId: String) {
        if (state.value.running) {
            update { it.copy(expanded = true) }
            return
        }
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return
        mutable.value = VoiceCallState(sessionId = sessionId, running = true)
        try {
            ContextCompat.startForegroundService(context, Intent(context, VoiceCallService::class.java)
                .setAction(VoiceCallService.START).putExtra("session_id", sessionId))
        } catch (_: RuntimeException) {
            failed(AppLocale.text("Не удалось включить фоновый разговор. Открой приложение и попробуй снова.", "Cannot start the background call. Open the app and try again."))
        }
    }

    fun show() { update { it.copy(expanded = true) } }
    fun minimize() { service?.cancelPushToTalk(); update { it.copy(expanded = false) } }
    fun close() {
        service?.finishCall()
        if (service == null) mutable.value = VoiceCallState()
    }
    fun failed(message: String) { update { it.copy(running = false, ready = false, phase = "error", error = message, expanded = true, speaking = false) } }
    fun command(id: String) { service?.javascript("document.getElementById('$id').click()") }

    fun attach(container: FrameLayout) {
        val view = service?.webView ?: return
        if (view.parent === container) return
        (view.parent as? ViewGroup)?.removeView(view)
        container.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }

    fun beginPushToTalk() { service?.javascript("window.IgorekVoice.beginPushToTalk()") }
    fun endPushToTalk() { service?.javascript("window.IgorekVoice.endPushToTalk()") }
    fun cancelPushToTalk() { service?.cancelPushToTalk() }
    fun setMicMode(mode: String) { if (mode in setOf("ptt", "continuous")) service?.javascript("window.IgorekVoice.setMicMode('$mode')") }
    fun detach(container: FrameLayout) { service?.cancelPushToTalk(); container.removeAllViews() }
}
