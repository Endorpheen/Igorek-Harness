package ru.igorek.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.serialization.encodeToString
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json

class NotificationsTest {

    @Test
    fun `a new outage with the same text retains a distinct persistent incident identity`() {
        val error = ErrorObservation(code = "NET.CONNECTION_RESET", owner = "fixture")
        val text = "Connection interrupted"
        var list = mergeNotification(emptyList(), text, "CONNECTION", 100, error)
        val firstId = list.single().id
        list = recoverNotifications(list, "fixture", 5000, 200)
        list = mergeNotification(list, text, "CONNECTION", 300, error.copy(eventId = "next-outage"))

        assertEquals(2, list.size)
        assertEquals(1, list.map { "${it.kind}|${it.text}" }.toSet().size)
        assertEquals(2, list.map { it.id }.toSet().size)
        assertEquals(firstId, list.first().id)
        assertEquals("recovered", list.first().state)
        assertEquals("observed", list.last().state)
        val restored = Json.decodeFromString<List<NotificationEntry>>(Json.encodeToString(list))
        assertEquals(list.map { it.id }, restored.map { it.id })
        assertEquals(listOf(restored.last()), visibleNotifications(restored))
        assertEquals(1, unreadNotificationCount(restored))
    }

    @Test
    fun `restored recovered incidents stay in diagnostics without cards or unread badge`() {
        val codes = listOf("STREAM.INTERRUPTED", "TLS.CONNECTION_INTERRUPTED", "NET.TRANSPORT_FAILURE",
            "NET.READ_TIMEOUT", "NET.CONNECTION_RESET", "NET.CONNECTION_REFUSED")
        val entries = (0 until 16).map { index ->
            val error = ErrorObservation(code = codes[index % codes.size], owner = "fixture")
            NotificationEntry("Recovered failure", state = "recovered", recoveredAt = 200,
                diagnostic = error, events = listOf(error), recoveryProof = "stable_ready")
        }
        val codec = Json { encodeDefaults = true }
        val restored = codec.decodeFromString<List<NotificationEntry>>(codec.encodeToString(entries))

        assertTrue(visibleNotifications(restored).isEmpty())
        assertEquals(0, unreadNotificationCount(restored))
        assertEquals(entries, restored)
        assertTrue(restored.all { !it.read && it.events.isNotEmpty() })
    }

    @Test
    fun `recovery removes only resolved cards while other owners and unknown sends stay visible`() {
        val error = ErrorObservation(code = "NET.CONNECTION_RESET", owner = "fixture")
        var entries = mergeNotification(emptyList(), "Connection failed", "CONNECTION", 100, error)
        entries = mergeNotification(entries, "Other connection", "CONNECTION", 100,
            error.copy(owner = "other", eventId = "other-error"))
        entries = mergeNotification(entries, "Send unconfirmed", "OPERATION", 100,
            error.copy(outcome = "unknown", operation = "send", clientOperationId = "message", eventId = "send-error"))
        entries = mergeNotification(entries, "Access rejected", "CONNECTION", 100,
            error.copy(code = "AUTH.REJECTED", owner = "auth", eventId = "auth-error"))

        assertEquals(4, unreadNotificationCount(entries))
        val recovered = recoverNotifications(entries, "fixture", ErrorCatalog.stableAfterMs, 6000)
        assertEquals(entries.drop(1), visibleNotifications(recovered))
        assertEquals(3, unreadNotificationCount(recovered))
        assertEquals(4, recovered.size)
        assertEquals("recovered", recovered.first().state)
    }

    @Test
    fun `separate owners with identical error text keep separate identities through acknowledgment`() {
        val error = ErrorObservation(code = "NET.CONNECTION_RESET", owner = "first-owner")
        var list = mergeNotification(emptyList(), "Connection interrupted", "CONNECTION", 100, error)
        list = mergeNotification(list, "Connection interrupted", "CONNECTION", 200,
            error.copy(owner = "second-owner", eventId = "other-owner-event"))
        val ids = list.map { it.id }
        assertEquals(2, ids.toSet().size)
        list = list.map { it.copy(read = true) }
        list = mergeNotification(list, "Translated text", "CONNECTION", 300,
            error.copy(eventId = "repeated-event"))
        assertEquals(ids, list.map { it.id })
        assertTrue(list.all { it.read })
    }

    @Test
    fun `repeat of the same error updates time and counter instead of adding a card`() {
        var list = mergeNotification(emptyList(), "Нет связи с сервером", "CONNECTION", at = 1_000L)
        list = mergeNotification(list, "Нет связи с сервером", "CONNECTION", at = 5_000L)

        assertEquals(1, list.size)
        val entry = list.single()
        assertEquals(1_000L, entry.firstSeen)
        assertEquals(5_000L, entry.lastSeen)
        assertEquals(2, entry.count)
    }

    @Test
    fun `different errors produce separate cards and newest stays last`() {
        var list = mergeNotification(emptyList(), "первая", "OPERATION", at = 1_000L)
        list = mergeNotification(list, "вторая", "OPERATION", at = 2_000L)

        assertEquals(listOf("первая", "вторая"), list.map { it.text })
    }

    @Test
    fun `blank messages are ignored and the log is bounded`() {
        assertEquals(0, mergeNotification(emptyList(), "   ", "OPERATION", at = 1L).size)

        var list = emptyList<NotificationEntry>()
        repeat(NOTIFICATION_LIMIT + 5) { index ->
            list = mergeNotification(list, "ошибка-$index", "OPERATION", at = index.toLong())
        }
        assertEquals(NOTIFICATION_LIMIT, list.size)
        assertTrue(list.last().text == "ошибка-${NOTIFICATION_LIMIT + 4}")
    }

    @Test
    fun `unread count tracks only unread entries`() {
        val list = listOf(
            NotificationEntry("a", "CONNECTION", read = false),
            NotificationEntry("b", "OPERATION", read = true),
            NotificationEntry("c", "OPERATION", read = false),
        )
        assertEquals(2, unreadNotificationCount(list))
        assertEquals(0, unreadNotificationCount(list.map { it.copy(read = true) }))
    }

    @Test
    fun `a read error stays read when the same error repeats`() {
        var list = mergeNotification(emptyList(), "Нет связи с сервером", "CONNECTION", at = 1_000L)
        list = list.map { it.copy(read = true) }
        list = mergeNotification(list, "Нет связи с сервером", "CONNECTION", at = 5_000L)

        assertEquals(1, list.size)
        assertTrue(list.single().read)
        assertEquals(0, unreadNotificationCount(list))
    }
}
