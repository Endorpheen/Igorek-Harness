package ru.igorek.remote.data

/**
 * One notification card in the notification list.
 *
 * Repeated observations update an active incident. A failure after recovery or
 * from another owner creates a separate incident, possibly with the same text.
 * [id] remains stable across updates, acknowledgment and history restoration.
 */
@kotlinx.serialization.Serializable
data class NotificationEntry(
    val text: String,
    val kind: String = "",
    val firstSeen: Long = 0L,
    val lastSeen: Long = 0L,
    val count: Int = 1,
    val read: Boolean = false,
    val id: String = java.util.UUID.randomUUID().toString(),
    val state: String = "observed",
    val recoveredAt: Long? = null,
    val diagnostic: ErrorObservation? = null,
    val events: List<ErrorObservation> = emptyList(),
    val recoveryProof: String = "",
    val quiet: Boolean = false,
)

internal fun unreadNotificationCount(entries: List<NotificationEntry>): Int =
    visibleNotifications(entries).count { !it.read }

/** How many cards we keep in the list to prevent it from growing infinitely. */
internal const val NOTIFICATION_LIMIT = 50

/**
 * Adds an observation to its active incident, or merges legacy messages by text.
 *
 * Returns a new list: the old entry is updated with the time of the last appearance and counter,
 * the new one is appended at the end.
 */
internal fun mergeNotification(
    entries: List<NotificationEntry>,
    text: String,
    kind: String,
    at: Long,
    diagnostic: ErrorObservation? = null,
    quiet: Boolean = false,
): List<NotificationEntry> {
    val message = text.trim()
    if (message.isEmpty()) return entries
    val safe = diagnostic?.let(::sanitizeDiagnostic)
    if (safe != null && safe.eventId.isNotBlank()) {
        val known = entries.indexOfFirst { it.diagnostic?.eventId == safe.eventId || it.events.any { event -> event.eventId == safe.eventId } }
        if (known >= 0) {
            val existing = entries[known]
            if (existing.state == "recovered") return entries
            return entries.toMutableList().also { it[known] = existing.copy(text = message, quiet = existing.quiet && quiet) }
        }
    }
    val nextState=if(safe==null)"observed" else if(safe.code=="TOOL.INVALID_ARGUMENTS" && safe.delivery=="rejected" && safe.outcome=="failed")"observed" else if(safe.outcome=="unknown" || ErrorCatalog.codes[safe.code]?.retryable==false)"needs_owner" else if(safe.nextRetryAt!=null)"recovering" else "observed"
    val index = entries.indexOfFirst { if(safe==null) it.diagnostic==null && it.text == message else
        it.state!="recovered" && it.diagnostic?.let(::diagnosticFingerprint)==diagnosticFingerprint(safe) }
    if (index < 0) {
        val appended = entries + NotificationEntry(message, kind, at, at, diagnostic=safe,state=nextState,events=safe?.let { listOf(it) } ?: emptyList(),quiet=quiet)
        return if (appended.size > NOTIFICATION_LIMIT) {
            val overflow = appended.size - NOTIFICATION_LIMIT
            val quietIndexes = appended.indices.filter { appended[it].quiet && isBackgroundEventTimeout(appended[it].diagnostic) }
                .take(overflow).toSet()
            appended.filterIndexed { index, _ -> index !in quietIndexes }.takeLast(NOTIFICATION_LIMIT)
        } else {
            appended
        }
    }
    val existing = entries[index]
    if(safe!=null && (safe.eventId==existing.diagnostic?.eventId || existing.events.any { it.eventId==safe.eventId }))return entries.toMutableList().also { it[index]=existing.copy(text=message,quiet=existing.quiet && quiet) }
    val updated = entries.toMutableList()
    updated[index] = existing.copy(
        kind = kind.ifEmpty { existing.kind },
        quiet = existing.quiet && quiet,
        lastSeen = at,
        count = (existing.count.toLong() + 1).coerceAtMost(1000000000).toInt(),
        diagnostic = safe ?: existing.diagnostic,
        state = nextState,
        events = if(safe==null)existing.events else (existing.events+safe).takeLast(8),
    )
    return updated
}

internal fun recoverNotifications(entries: List<NotificationEntry>, owner: String, stableMs: Long, at: Long,
    operationCompleted: Boolean = false): List<NotificationEntry> {
    if(!operationCompleted && stableMs<ErrorCatalog.stableAfterMs)return entries
    return entries.map { if(it.diagnostic?.owner==owner && it.diagnostic.outcome!="unknown" && it.state!="recovered")
        it.copy(state="recovered",recoveredAt=at,recoveryProof=if(operationCompleted)"operation_completed" else "stable_ready") else it }
}

internal fun confirmNotifications(entries:List<NotificationEntry>, operationId:String, taskId:String, at:Long):List<NotificationEntry> =
    entries.map { entry -> if(entry.diagnostic?.clientOperationId==operationId && entry.diagnostic.outcome=="unknown")
        entry.copy(text=AppLocale.text("Сервер подтвердил получение сообщения. Повторная отправка не выполнялась.","The server confirmed receipt of the message. It was not sent again."),state="recovered",recoveredAt=at,recoveryProof="operation_confirmed",diagnostic=entry.diagnostic.copy(delivery="accepted",outcome="accepted",taskId=taskId)) else entry }
