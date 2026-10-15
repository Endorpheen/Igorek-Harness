package ru.igorek.remote.ui

import ru.igorek.remote.data.ArchiveSessionsResponse
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.SessionSummary
import org.junit.Assert.assertEquals
import org.junit.Test

class ComponentsTest {
    @Test
    fun `duration is compact and readable`() {
        assertEquals("", formatDuration(0))
        assertEquals("322 мс", formatDuration(322))
        assertEquals("1.2 с", formatDuration(1200))
    }

    @Test
    fun `chat count uses russian plural forms`() {
        assertEquals("1 чат", chatCountLabel(1))
        assertEquals("2 чата", chatCountLabel(2))
        assertEquals("5 чатов", chatCountLabel(5))
        assertEquals("11 чатов", chatCountLabel(11))
        assertEquals("21 чат", chatCountLabel(21))
    }

    @Test
    fun `archive result reports skipped active sessions`() {
        val result = ArchiveSessionsResponse(updated = 12, skippedActive = 2)

        assertEquals("Архивировано 12 чатов. Активных пропущено: 2.", archiveResultText(result))
    }

    @Test
    fun `session activity uses exact backend status`() {
        assertEquals(
            "останавливается",
            sessionStatusLabel(SessionSummary("one", active = true, activeStatus = "cancelling")),
        )
        assertEquals(
            "ждёт подтверждения",
            sessionStatusLabel(SessionSummary("two", active = true, activeStatus = "waiting_approval")),
        )
        assertEquals(
            "остановлена",
            sessionStatusLabel(SessionSummary("three", lastTaskStatus = "dead_letter")),
        )
    }

    @Test
    fun `tool card statuses keep exact backend values`() {
        assertEquals("работает", toolStatusLabel("running"))
        assertEquals("готово", toolStatusLabel("done"))
        assertEquals("ожидает решения", toolStatusLabel("waiting_approval"))
        assertEquals("не выполнено", toolStatusLabel("deferred"))
        assertEquals("отклонено", toolStatusLabel("rejected"))
    }

    @Test
    fun `english localization covers counters statuses and durations`() {
        AppLocale.set(AppLanguage.English)
        try {
            assertEquals("2 chats", chatCountLabel(2, AppLanguage.English))
            assertEquals("1.2 s", formatDuration(1200))
            assertEquals(
                "waiting for approval",
                sessionStatusLabel(
                    SessionSummary("one", active = true, activeStatus = "waiting_approval"),
                    AppLanguage.English,
                ),
            )
            assertEquals(
                "Archived 12 chats. Active chats skipped: 2.",
                archiveResultText(ArchiveSessionsResponse(updated = 12, skippedActive = 2)),
            )
        } finally {
            AppLocale.set(AppLanguage.Russian)
        }
    }
}
