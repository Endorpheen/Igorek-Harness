package ru.igorek.remote.ui

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Test
import ru.igorek.remote.data.AppLanguage

class TopBarStatusTest {
    @Test
    fun `connection status colour is green online and red otherwise`() {
        assertEquals(Color(0xFF44D7A8), connectionStatusColor("online"))
        assertEquals(Color(0xFFFF667A), connectionStatusColor("offline"))
        assertEquals(Color(0xFFFF667A), connectionStatusColor("connecting"))
        assertEquals(Color(0xFFFF667A), connectionStatusColor(null))
        assertEquals(Color(0xFFFF667A), connectionStatusColor("Online"))
    }

    @Test
    fun `status label prefers non blank fallback over connection state`() {
        assertEquals(
            "Восстанавливаем связь…",
            connectionStatusLabel("offline", "Восстанавливаем связь…", AppLanguage.Russian),
        )
        assertEquals(
            "Reconnecting…",
            connectionStatusLabel("connecting", "Reconnecting…", AppLanguage.English),
        )
        assertEquals("На связи", connectionStatusLabel("offline", "На связи", AppLanguage.Russian))
    }

    @Test
    fun `status label falls back to bilingual connection defaults`() {
        assertEquals("На связи", connectionStatusLabel("online", null, AppLanguage.Russian))
        assertEquals("Online", connectionStatusLabel("online", null, AppLanguage.English))
        assertEquals("На связи", connectionStatusLabel("online", "   ", AppLanguage.Russian))
        assertEquals("Нет соединения", connectionStatusLabel("offline", null, AppLanguage.Russian))
        assertEquals("Offline", connectionStatusLabel("connecting", null, AppLanguage.English))
        assertEquals("Нет соединения", connectionStatusLabel(null, null, AppLanguage.Russian))
    }
}
