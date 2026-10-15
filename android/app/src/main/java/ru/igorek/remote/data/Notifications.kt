package ru.igorek.remote.data

/**
 * One notification card in the notification list.
 *
 * Repeating the same error does not create a new entry: [lastSeen] and
 * [count] are updated. This shows the time of the last appearance in the interface, allowing you to distinguish
 * an old error from a fresh one.
 */
data class NotificationEntry(
    val text: String,
    val kind: String = "",
    val firstSeen: Long = 0L,
    val lastSeen: Long = 0L,
    val count: Int = 1,
    val read: Boolean = false,
)

internal fun unreadNotificationCount(entries: List<NotificationEntry>): Int =
    entries.count { !it.read }

/** How many cards we keep in the list to prevent it from growing infinitely. */
internal const val NOTIFICATION_LIMIT = 50

/**
 * Adds a message to the feed, merging duplicates based on text.
 *
 * Returns a new list: the old entry is updated with the time of the last appearance and counter,
 * the new one is appended at the end.
 */
internal fun mergeNotification(
    entries: List<NotificationEntry>,
    text: String,
    kind: String,
    at: Long,
): List<NotificationEntry> {
    val message = text.trim()
    if (message.isEmpty()) return entries
    val index = entries.indexOfFirst { it.text == message }
    if (index < 0) {
        val appended = entries + NotificationEntry(message, kind, at, at)
        return if (appended.size > NOTIFICATION_LIMIT) {
            appended.takeLast(NOTIFICATION_LIMIT)
        } else {
            appended
        }
    }
    val existing = entries[index]
    val updated = entries.toMutableList()
    updated[index] = existing.copy(
        kind = kind.ifEmpty { existing.kind },
        lastSeen = at,
        count = existing.count + 1,
    )
    return updated
}
