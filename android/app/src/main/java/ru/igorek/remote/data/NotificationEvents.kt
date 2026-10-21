package ru.igorek.remote.data

import kotlinx.serialization.json.*
import kotlinx.serialization.decodeFromString

internal fun shouldObserveNotificationEvent(event: BridgeEvent, replayThrough: Long, lastProcessed: Long): Boolean =
    event.seq?.let { it > maxOf(replayThrough, lastProcessed) } ?: true

internal fun isTimelinePresentationNotification(entry: NotificationEntry): Boolean = entry.diagnostic?.let {
    it.endpointRole == "local_ui" && it.operation == "display" && it.phase == "presentation" && it.owner.startsWith("ui:timeline:")
} == true

internal fun notificationEventFailure(event: BridgeEvent, known: List<NotificationEntry>): ErrorObservation? {
    fun value(key: String) = (event.payload[key] as? JsonPrimitive)?.contentOrNull.orEmpty()
    val failedStatus = value("status") in setOf("fail", "failed", "error", "dead_letter", "timeout")
    val failure = when (event.kind) {
        "network.retry", "task.error", "compaction.failed" -> true
        "task.status", "tool.result" -> failedStatus
        "task.finish" -> failedStatus && value("reason") !in setOf("interrupted", "approval_required") &&
            ((event.payload["approval_blocked_count"] as? JsonPrimitive)?.longOrNull ?: 0) == 0L
        else -> false
    }
    if (!failure) return null
    val codec = Json { ignoreUnknownKeys = true }
    val typed = event.payload["diagnostic"]?.let { runCatching { codec.decodeFromString<ErrorObservation>(it.toString()) }.getOrNull() }
    val task = event.taskId.orEmpty().ifBlank { value("task_id") }
    val session = event.sessionId.orEmpty()
    if (typed != null) return sanitizeDiagnostic(typed.copy(
        taskId = typed.taskId.ifBlank { task }, sessionId = typed.sessionId.ifBlank { session }))
    if (event.kind in setOf("network.retry", "task.status")) return null
    if (event.kind in setOf("task.error", "task.finish") && task.isNotBlank() && known.any {
        it.state != "recovered" && it.diagnostic?.let { error -> error.taskId == task && error.sessionId == session && error.endpointRole != "local_ui" } == true
    }) return null
    val source = listOf(event.kind, event.seq, event.ts, session, task, event.payload.toString()).joinToString("|")
    val identity = if (event.kind == "tool.result") value("tool_use_id").ifBlank { value("call_id") } else ""
    return ErrorObservation(eventId = "event:" + connectionScope(source), observedAt = event.ts ?: System.currentTimeMillis(),
        code = "ERROR.UNKNOWN", component = "task", operation = "execute", endpointRole = "bridge_to_mobile",
        owner = "task:" + connectionScope(listOf(session, task, identity).joinToString("|")),
        phase = event.kind.replace('.', '_'), taskId = task, sessionId = session)
}
