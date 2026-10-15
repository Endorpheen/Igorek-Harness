package ru.igorek.remote.ui

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import ru.igorek.remote.data.AppLanguage

// The accent color in the header takes the app colors, and the phase-of-work label matches the
// semantic indicator’s description, so we validate them without launching Compose.
class QuotaUiTest {
    @Test
    fun `quota tone colours match the app palette`() {
        assertEquals(Color(0xFF44D7A8), quotaToneColor(ru.igorek.remote.data.QuotaTone.Ok))
        assertEquals(Color(0xFFF6B94A), quotaToneColor(ru.igorek.remote.data.QuotaTone.Warn))
        assertEquals(Color(0xFFFF667A), quotaToneColor(ru.igorek.remote.data.QuotaTone.Danger))
    }

    @Test
    fun `work phase label is silent when idle`() {
        assertNull(workPhaseLabel(WorkPhase.Idle, AppLanguage.Russian))
        assertEquals("Игорёк работает", workPhaseLabel(WorkPhase.Working, AppLanguage.Russian))
        assertEquals("Igorek is working", workPhaseLabel(WorkPhase.Working, AppLanguage.English))
        assertEquals("Игорёк останавливается", workPhaseLabel(WorkPhase.Stopping, AppLanguage.Russian))
        assertEquals(
            "Igorek is waiting for approval",
            workPhaseLabel(WorkPhase.WaitingApproval, AppLanguage.English),
        )
    }
}
