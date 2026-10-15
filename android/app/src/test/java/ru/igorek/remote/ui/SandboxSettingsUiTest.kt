package ru.igorek.remote.ui

import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.SANDBOX_REASON_BWRAP_MISSING
import ru.igorek.remote.data.SANDBOX_REASON_ENV_DISABLED
import ru.igorek.remote.data.SANDBOX_REASON_NAMESPACE_BLOCKED
import ru.igorek.remote.data.SANDBOX_REASON_OBSERVE
import ru.igorek.remote.data.SANDBOX_REASON_USER_DISABLED
import ru.igorek.remote.data.SandboxSettingsResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SandboxSettingsUiTest {
    @Test
    fun `active sandbox label explains allowed folders`() {
        val settings = SandboxSettingsResponse()

        assertEquals(
            "Активна — запись только в разрешённые папки",
            sandboxStatusLabel(settings, AppLanguage.Russian),
        )
        assertEquals(
            "Active — writes only to allowed folders",
            sandboxStatusLabel(settings, AppLanguage.English),
        )
    }

    @Test
    fun `disabled labels cover user and env reasons`() {
        val userDisabled = SandboxSettingsResponse(
            enabled = false,
            active = false,
            reason = SANDBOX_REASON_USER_DISABLED,
        )
        val envDisabled = SandboxSettingsResponse(
            enabled = false,
            active = false,
            reason = SANDBOX_REASON_ENV_DISABLED,
        )

        assertEquals("Отключена", sandboxStatusLabel(userDisabled, AppLanguage.Russian))
        assertEquals("Disabled", sandboxStatusLabel(envDisabled, AppLanguage.English))
        assertEquals("отключена в настройках", sandboxReasonLabel(SANDBOX_REASON_USER_DISABLED, AppLanguage.Russian))
        assertEquals(
            "disabled by server configuration",
            sandboxReasonLabel(SANDBOX_REASON_ENV_DISABLED, AppLanguage.English),
        )
    }

    @Test
    fun `unavailable labels mention the bwrap reason`() {
        val missing = SandboxSettingsResponse(
            enabled = false,
            active = false,
            available = false,
            reason = SANDBOX_REASON_BWRAP_MISSING,
        )
        val blocked = SandboxSettingsResponse(
            enabled = true,
            active = false,
            available = false,
            reason = SANDBOX_REASON_NAMESPACE_BLOCKED,
        )

        val missingRu = sandboxStatusLabel(missing, AppLanguage.Russian)
        assertTrue(missingRu.startsWith("Недоступна: "))
        assertTrue(missingRu.contains("bwrap"))
        assertTrue(missingRu.contains(SANDBOX_REASON_BWRAP_MISSING))

        val blockedEn = sandboxStatusLabel(blocked, AppLanguage.English)
        assertTrue(blockedEn.startsWith("Unavailable: "))
        assertTrue(blockedEn.contains(SANDBOX_REASON_NAMESPACE_BLOCKED))

        val noReason = SandboxSettingsResponse(available = false, active = false)
        assertTrue(sandboxStatusLabel(noReason, AppLanguage.Russian).startsWith("Недоступна: "))
    }

    @Test
    fun `observe mode has its own label`() {
        val observe = SandboxSettingsResponse(reason = SANDBOX_REASON_OBSERVE)

        assertEquals("Режим наблюдения", sandboxStatusLabel(observe, AppLanguage.Russian))
        assertEquals("Observe mode", sandboxStatusLabel(observe, AppLanguage.English))
    }
}
