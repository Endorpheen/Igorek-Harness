package ru.igorek.remote.ui

import java.time.Instant
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.CheckpointDto

class CheckpointsUiTest {
    private val utc = ZoneId.of("UTC")
    private val now = Instant.parse("2026-09-21T12:00:00Z").toEpochMilli()

    @Test
    fun `checkpoint prefix is stripped from the reason`() {
        assertEquals("before edit", stripCheckpointPrefix("checkpoint: before edit"))
        assertEquals("before edit", stripCheckpointPrefix("Checkpoint:before edit"))
        assertEquals("just a reason", stripCheckpointPrefix("just a reason"))
        assertEquals("", stripCheckpointPrefix("checkpoint:"))
        assertEquals("", stripCheckpointPrefix("  "))
    }

    @Test
    fun `short hash prefers the backend short value and falls back to the hash head`() {
        assertEquals("abcdef1", checkpointShortLabel("abcdef1234567890", "abcdef1"))
        assertEquals("abcdef1", checkpointShortLabel("abcdef1234567890", " "))
        assertEquals("", checkpointShortLabel("", ""))
    }

    @Test
    fun `time label shows today yesterday and dated versions`() {
        assertEquals("сегодня, 09:30", checkpointTimeLabel("2026-09-21T09:30:00Z", AppLanguage.Russian, now, utc))
        assertEquals("today, 09:30", checkpointTimeLabel("2026-09-21T09:30:00Z", AppLanguage.English, now, utc))
        assertEquals("вчера, 23:10", checkpointTimeLabel("2026-09-20T23:10:00Z", AppLanguage.Russian, now, utc))
        assertEquals("yesterday, 23:10", checkpointTimeLabel("2026-09-20T23:10:00Z", AppLanguage.English, now, utc))
        assertEquals("01.09.2026 05:05", checkpointTimeLabel("2026-09-01T05:05:00Z", AppLanguage.Russian, now, utc))
        assertEquals("01.09.2026 05:05", checkpointTimeLabel("2026-09-01T05:05:00Z", AppLanguage.English, now, utc))
    }

    @Test
    fun `time label survives offsets local timestamps and broken input`() {
        assertEquals("15.09.2026 09:30", checkpointTimeLabel("2026-09-15T09:30:00+00:00", AppLanguage.Russian, now, utc))
        assertEquals("сегодня, 09:30", checkpointTimeLabel("2026-09-21T09:30:00", AppLanguage.Russian, now, utc))
        assertEquals("not-a-date", checkpointTimeLabel("not-a-date", AppLanguage.Russian, now, utc))
        assertEquals("", checkpointTimeLabel("", AppLanguage.Russian, now, utc))
        assertNull(checkpointInstant("  ", utc))
    }

    @Test
    fun `empty state only appears when history is unavailable`() {
        assertNull(checkpointsEmptyState(true, AppLanguage.Russian))
        assertEquals("История файлов недоступна", checkpointsEmptyState(false, AppLanguage.Russian))
        assertEquals("File history unavailable", checkpointsEmptyState(false, AppLanguage.English))
    }

    @Test
    fun `footer appears only with more pages and pluralises versions`() {
        assertNull(checkpointsFooterLabel(50, false, AppLanguage.Russian))
        assertNull(checkpointsFooterLabel(0, false, AppLanguage.English))
        assertEquals("Показаны последние 50 версий", checkpointsFooterLabel(50, true, AppLanguage.Russian))
        assertEquals("Показаны последние 1 версия", checkpointsFooterLabel(1, true, AppLanguage.Russian))
        assertEquals("Показаны последние 2 версии", checkpointsFooterLabel(2, true, AppLanguage.Russian))
        assertEquals("Показаны последние 11 версий", checkpointsFooterLabel(11, true, AppLanguage.Russian))
        assertEquals("Показаны последние 21 версия", checkpointsFooterLabel(21, true, AppLanguage.Russian))
        assertEquals("Showing the last 1 version", checkpointsFooterLabel(1, true, AppLanguage.English))
        assertEquals("Showing the last 10 versions", checkpointsFooterLabel(10, true, AppLanguage.English))
    }

    @Test
    fun `changes dialog title names the version`() {
        val checkpoint = CheckpointDto(hash = "a".repeat(40), shortHash = "abc1234")
        assertEquals("Файлы версии abc1234", checkpointChangesTitle(checkpoint, AppLanguage.Russian))
        assertEquals("Files in version abc1234", checkpointChangesTitle(checkpoint, AppLanguage.English))
        assertEquals("Файлы версии", checkpointChangesTitle(CheckpointDto(), AppLanguage.Russian))
        assertEquals("Files in version", checkpointChangesTitle(CheckpointDto(), AppLanguage.English))
    }

    @Test
    fun `diff subtitle names the version and the current state`() {
        assertNull(checkpointDiffSubtitle("", "", AppLanguage.Russian))
        assertEquals("версия abc1234 ↔ сейчас", checkpointDiffSubtitle("", "abc1234", AppLanguage.Russian))
        assertEquals("version abc1234 ↔ now", checkpointDiffSubtitle("", "abc1234", AppLanguage.English))
        assertEquals("версия abcdef12 ↔ сейчас", checkpointDiffSubtitle("abcdef1234567890", "zzz", AppLanguage.Russian))
    }

    @Test
    fun `drawer target follows fling velocity and drag distance`() {
        assertFalse(checkpointsDrawerTarget(offsetPx = 400f, widthPx = 400f, velocity = 0f))
        assertTrue(checkpointsDrawerTarget(offsetPx = 400f, widthPx = 400f, velocity = -1200f))
        assertFalse(checkpointsDrawerTarget(offsetPx = 0f, widthPx = 400f, velocity = 1200f))
        assertTrue(checkpointsDrawerTarget(offsetPx = 100f, widthPx = 400f, velocity = 0f))
        assertFalse(checkpointsDrawerTarget(offsetPx = 200f, widthPx = 400f, velocity = 0f))
        assertTrue(checkpointsDrawerTarget(offsetPx = 0f, widthPx = 0f, velocity = 0f))
    }

    @Test
    fun `drawer target opens on a left fling and closes on a right fling at the threshold`() {
        assertTrue(
            checkpointsDrawerTarget(
                offsetPx = 400f,
                widthPx = 400f,
                velocity = -CHECKPOINTS_FLING_VELOCITY,
            ),
        )
        assertTrue(
            checkpointsDrawerTarget(
                offsetPx = 400f,
                widthPx = 400f,
                velocity = -CHECKPOINTS_FLING_VELOCITY - 1f,
            ),
        )
        assertFalse(
            checkpointsDrawerTarget(
                offsetPx = 400f,
                widthPx = 400f,
                velocity = CHECKPOINTS_FLING_VELOCITY,
            ),
        )
        assertFalse(
            checkpointsDrawerTarget(
                offsetPx = 400f,
                widthPx = 400f,
                velocity = CHECKPOINTS_FLING_VELOCITY + 1f,
            ),
        )
    }

    @Test
    fun `drawer target falls back to the open fraction for slow releases`() {
        val width = 400f
        val threshold = width * CHECKPOINTS_OPEN_FRACTION
        assertTrue(checkpointsDrawerTarget(offsetPx = threshold - 0.1f, widthPx = width, velocity = -1f))
        assertFalse(checkpointsDrawerTarget(offsetPx = threshold, widthPx = width, velocity = -1f))
        assertTrue(checkpointsDrawerTarget(offsetPx = threshold - 0.1f, widthPx = width, velocity = 1f))
        assertFalse(checkpointsDrawerTarget(offsetPx = threshold, widthPx = width, velocity = 1f))
    }

    @Test
    fun `drawer opens again after being closed by the X button`() {
        // Regression: with the old hosted drawer the second right-to-left swipe never
        // reached the panel because the settle animation cancelled the gesture coroutine.
        // The decision path must keep returning true for identical swipe input.
        val width = 400f
        val swipeToOpen = { checkpointsDrawerTarget(offsetPx = 60f, widthPx = width, velocity = -400f) }

        var open = swipeToOpen()
        assertTrue(open)

        open = false
        assertFalse(open)

        open = swipeToOpen()
        assertTrue(open)

        // Closing still works after the reopen cycle.
        assertFalse(checkpointsDrawerTarget(offsetPx = 200f, widthPx = width, velocity = 0f))
        assertFalse(checkpointsDrawerTarget(offsetPx = 10f, widthPx = width, velocity = CHECKPOINTS_FLING_VELOCITY))
    }
}
