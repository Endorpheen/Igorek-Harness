package ru.igorek.remote.service

internal data class VoiceCallState(
    val sessionId: String = "",
    val running: Boolean = false,
    val expanded: Boolean = true,
    val ready: Boolean = false,
    val phase: String = "connecting",
    val notice: String = "",
    val connectedAt: Long = 0,
    val connected: Boolean = false,
    val speaking: Boolean = false,
    val waitingForAgent: Boolean = false,
    val micMuted: Boolean = true,
    val micMode: String = "ptt",
    val pttRecording: Boolean = false,
    val speakerMuted: Boolean = false,
    val approval: Boolean = false,
    val error: String = "",
) {
    fun transport(phase: String, connected: Boolean, now: Long): VoiceCallState = copy(
        phase = phase,
        connected = connected,
        connectedAt = if (connected && connectedAt == 0L) now else connectedAt,
    )

    val ringing: Boolean get() = running && connectedAt == 0L && phase == "connecting"
    val holdTone: Boolean get() = running && connected && waitingForAgent && !speakerMuted && !speaking && !pttRecording && !approval && phase != "error" && phase != "reconnecting"
    val showOverlay: Boolean get() = running && sessionId.isNotEmpty()
}
