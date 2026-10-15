package ru.igorek.remote.data

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

// Pure logic for the OpenCode quota card: numbers come from the server exactly as in the web panel, while the tone, signatures, and reset expiration are calculated on the client side.
class QuotaTest {
    @Test fun codexShowsOnlyTheWeeklyWindow() {
        val windows=quotaWindows(QuotaUsageDto(weekly=QuotaWindowDto(percent=32.0)), "codex")
        assertEquals(listOf(QuotaWindowKind.Weekly), windows.map { it.kind })
        assertEquals(32.0, windows.single().percent!!, 0.001)
    }

    private fun iso(millis: Long): String = Instant.ofEpochMilli(millis).toString()

    @Test
    fun `windows follow rolling weekly monthly order`() {
        val usage = QuotaUsageDto(
            rolling = QuotaWindowDto(percent = 5.0, status = "ok", resetsAt = iso(1_000)),
            weekly = QuotaWindowDto(percent = 26.0, status = "ok", resetsAt = iso(2_000)),
            monthly = QuotaWindowDto(percent = 59.0, status = "ok", resetsAt = iso(3_000)),
        )

        val windows = quotaWindows(usage)

        assertEquals(
            listOf(QuotaWindowKind.Rolling, QuotaWindowKind.Weekly, QuotaWindowKind.Monthly),
            windows.map { it.kind },
        )
        assertEquals(5.0, windows[0].percent!!, 0.001)
        assertEquals("ok", windows[2].status)
    }

    @Test
    fun `missing windows degrade to empty entries`() {
        val windows = quotaWindows(QuotaUsageDto())

        assertEquals(3, windows.size)
        for (window in windows) {
            assertNull(window.percent)
            assertEquals("", window.status)
            assertEquals("", window.resetsAt)
        }
        assertEquals("—", quotaPercentText(null))
        assertEquals("", quotaFreeText(null))
    }

    @Test
    fun `tone thresholds match the web panel`() {
        assertEquals(QuotaTone.Ok, quotaTone(0.0, "ok"))
        assertEquals(QuotaTone.Ok, quotaTone(59.9, "ok"))
        assertEquals(QuotaTone.Warn, quotaTone(60.0, "ok"))
        assertEquals(QuotaTone.Warn, quotaTone(84.9, "ok"))
        assertEquals(QuotaTone.Danger, quotaTone(85.0, "ok"))
        assertEquals(QuotaTone.Danger, quotaTone(12.0, "rate-limited"))
        assertEquals(QuotaTone.Danger, quotaTone(null, "RATE-LIMITED"))
        assertEquals(QuotaTone.Ok, quotaTone(null, ""))
    }

    @Test
    fun `percent and free space are rounded to whole numbers`() {
        assertEquals("5%", quotaPercentText(5.4))
        assertEquals("26%", quotaPercentText(25.6))
        assertEquals("74% свободно", quotaFreeText(26.0))
        assertEquals("100% свободно", quotaFreeText(-3.0))
    }

    @Test
    fun `reset text covers minutes hours and calendar dates`() {
        val now = 1_800_000_000_000L

        assertEquals("сброс вот-вот", quotaResetText(iso(now + 30_000), now, AppLanguage.Russian))
        assertEquals("resets shortly", quotaResetText(iso(now + 30_000), now, AppLanguage.English))
        assertEquals("сброс через 5 мин", quotaResetText(iso(now + 5 * 60_000), now, AppLanguage.Russian))
        assertEquals("resets in 5 min", quotaResetText(iso(now + 5 * 60_000), now, AppLanguage.English))
        assertEquals("сброс через 2 ч", quotaResetText(iso(now + 120 * 60_000), now, AppLanguage.Russian))
        assertEquals(
            "сброс через 47 ч 30 мин",
            quotaResetText(iso(now + (47 * 60 + 30) * 60_000), now, AppLanguage.Russian),
        )
        val far = quotaResetText(iso(now + 5 * 24 * 60 * 60_000L), now, AppLanguage.Russian)
        assertTrue(far, far.startsWith("сброс "))
        assertFalse(far, far.contains("через"))
    }

    @Test
    fun `unparsable reset time is silent`() {
        assertEquals("", quotaResetText("", 1L, AppLanguage.Russian))
        assertEquals("", quotaResetText("не дата", 1L, AppLanguage.Russian))
        assertNull(parseInstantMillis("  "))
    }

    @Test
    fun `status text localizes only the rate limited flag`() {
        assertEquals("", quotaStatusText("", AppLanguage.Russian))
        assertEquals("", quotaStatusText("ok", AppLanguage.English))
        assertEquals("лимит исчерпан", quotaStatusText("rate-limited", AppLanguage.Russian))
        assertEquals("limit reached", quotaStatusText("rate-limited", AppLanguage.English))
        assertEquals("throttled", quotaStatusText("throttled", AppLanguage.Russian))
    }

    @Test
    fun `error codes map to client side wording`() {
        assertEquals(
            "Ключ OpenCode не найден на компьютере",
            quotaErrorText("no_key", AppLanguage.Russian),
        )
        assertEquals(
            "An OpenCode Go subscription is required",
            quotaErrorText("subscription_required", AppLanguage.English),
        )
        assertEquals("Не удалось получить квоту", quotaErrorText(null, AppLanguage.Russian))
        assertEquals("Не удалось получить квоту", quotaErrorText("unknown_code", AppLanguage.Russian))
        for (code in QUOTA_ERROR_CODES) {
            assertTrue(code, quotaErrorText(code, AppLanguage.Russian).isNotBlank())
            assertTrue(code, quotaErrorText(code, AppLanguage.English).isNotBlank())
        }
    }

    @Test
    fun `cache freshness follows the server ttl`() {
        val now = 1_800_000_000_000L

        assertTrue(quotaCacheFresh(now - 59_000, now))
        assertFalse(quotaCacheFresh(now - 60_000, now))
        assertFalse(quotaCacheFresh(0L, now))
    }
}
