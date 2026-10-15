package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull

internal data class VoiceApprovalDecision(val taskId: String, val requestId: String, val decision: String)

internal fun voiceApprovalDecision(path: String, body: String?): VoiceApprovalDecision? {
    if (body == null) return null
    val target = Regex("/api/tasks/([A-Za-z0-9_-]+)/approvals/([A-Za-z0-9_-]+)").matchEntire(path) ?: return null
    val decision = runCatching { Json.parseToJsonElement(body).jsonObject["decision"]?.jsonPrimitive?.contentOrNull }.getOrNull()
    if (decision !in setOf("allow_once", "allow_session", "reject")) return null
    return VoiceApprovalDecision(target.groupValues[1], target.groupValues[2], decision!!)
}

internal fun shouldForwardVoiceEvent(event: BridgeEvent, replayThrough: Long): Boolean {
    if (event.kind in setOf("approval.required", "approval.resolved")) return true
    if (event.seq != null && event.seq <= replayThrough) return false
    return event.kind in setOf("model.round_response", "task.error", "task.status", "task.finish")
}
