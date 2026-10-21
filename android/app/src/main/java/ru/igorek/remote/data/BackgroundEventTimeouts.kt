package ru.igorek.remote.data

internal const val BACKGROUND_EVENT_GRACE_MS = 15000L

internal fun isBackgroundEventTimeout(error: ErrorObservation?): Boolean = error != null &&
    error.code == "NET.READ_TIMEOUT" && error.component == "mobile-events" && error.operation == "read" &&
    error.endpointRole == "mobile_to_bridge" && error.phase == "sse_read" &&
    error.owner.startsWith("bridge-events:") && error.taskId.isEmpty() && error.sessionId.isEmpty() &&
    error.clientOperationId.isEmpty() && error.httpStatus == null && error.outcome == "failed"

internal fun shouldQuietBackgroundTimeout(error: ErrorObservation, visible: Boolean, resumedAgoMs: Long): Boolean =
    isBackgroundEventTimeout(error) && (!visible || resumedAgoMs in 0 until BACKGROUND_EVENT_GRACE_MS)

internal fun visibleNotifications(entries: List<NotificationEntry>): List<NotificationEntry> =
    entries.filterNot { it.state == "recovered" || (it.quiet && isBackgroundEventTimeout(it.diagnostic)) }

internal fun promoteBackgroundTimeouts(entries: List<NotificationEntry>, owner: String): List<NotificationEntry> =
    entries.map { if (it.quiet && it.state != "recovered" && it.diagnostic?.owner == owner &&
        isBackgroundEventTimeout(it.diagnostic)) it.copy(quiet = false, read = false) else it }
