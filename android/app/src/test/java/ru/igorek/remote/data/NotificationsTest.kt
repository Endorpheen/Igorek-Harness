package ru.igorek.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationsTest {

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
