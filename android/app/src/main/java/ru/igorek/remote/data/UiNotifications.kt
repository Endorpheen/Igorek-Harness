package ru.igorek.remote.data

import java.util.UUID

internal fun mergeUiNotification(entries: List<NotificationEntry>, message: String, owner: String, at: Long): List<NotificationEntry> {
    val text = safeDiagnosticText(message.trim())
    if (text.isBlank() || entries.any { it.diagnostic != null && it.diagnostic.endpointRole != "local_ui" && it.text == text }) return entries
    val eventId = UUID.nameUUIDFromBytes((owner + "\u0000" + text).toByteArray(Charsets.UTF_8)).toString()
    return mergeNotification(entries, text, "LOCAL", at,
        ErrorObservation(eventId = eventId, observedAt = at, code = "INTERNAL.UNEXPECTED",
            owner = "ui:" + owner, operation = "display", endpointRole = "local_ui", phase = "presentation"))
}

internal fun notificationFailureText(item: TimelineItem): String? = when {
    item is TimelineItem.Status && item.level == "error" -> listOf(item.text, item.details).filter { it.isNotBlank() }.joinToString("\n")
    item is TimelineItem.ToolCall && item.status in setOf("error", "fail", "failed") -> listOf(item.name, item.resultPreview).filter { it.isNotBlank() }.joinToString("\n")
    item is TimelineItem.Compaction && item.phase == "failed" -> listOf(item.summary, item.reason).filter { it.isNotBlank() }.joinToString("\n")
    else -> null
}
