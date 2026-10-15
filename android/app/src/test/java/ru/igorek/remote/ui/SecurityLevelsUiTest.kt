package ru.igorek.remote.ui

import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.SecuritySettingsResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SecurityLevelsUiTest {
    @Test
    fun `level labels cover all four levels`() {
        assertEquals("Стандартный", securityLevelLabel("standard", AppLanguage.Russian))
        assertEquals("Строгий", securityLevelLabel("strict", AppLanguage.Russian))
        assertEquals("Доверенный", securityLevelLabel("trusted", AppLanguage.Russian))
        assertEquals("Полный доступ", securityLevelLabel("full", AppLanguage.Russian))
        assertEquals("Full access", securityLevelLabel("full", AppLanguage.English))
    }

    @Test
    fun `unknown level is labelled as standard`() {
        assertEquals("Стандартный", securityLevelLabel("root", AppLanguage.Russian))
    }

    @Test
    fun `expiry text shows remaining time`() {
        val settings = SecuritySettingsResponse(
            level = "trusted",
            baselineLevel = "standard",
            isWeakened = true,
            expiresAt = 3600,
        )

        val text = securityExpiryText(settings, AppLanguage.Russian, nowSeconds = 0)
        assertTrue(text!!.contains("1 ч"))

        val expired = securityExpiryText(settings, AppLanguage.Russian, nowSeconds = 4000)
        assertEquals("срок истёк", expired)
    }
}
