package ru.igorek.remote.ui

import org.junit.Assert.*
import org.junit.Test
import ru.igorek.remote.data.*

class ChangedFilePresentationTest {
    @Test fun failedSessionStatusDoesNotRepeatErrorsOutsideTheCenter() {
        assertEquals("finished", sessionStatusLabel(SessionSummary("failed", lastTaskStatus = "fail"), AppLanguage.English))
        assertEquals("finished", sessionStatusLabel(SessionSummary("timeout", lastTaskStatus = "timeout"), AppLanguage.English))
    }
    @Test fun mediaTypesHaveLabelsInsteadOfLineCounts() {
        assertEquals("Video", nonTextChangeLabel("output/CLIP.MP4", language = AppLanguage.English))
        assertEquals("Аудио", nonTextChangeLabel("track.opus", language = AppLanguage.Russian))
        assertEquals("Image", nonTextChangeLabel("photo.png", language = AppLanguage.English))
        assertNull(nonTextChangeLabel("script.py"))
        assertNull(nonTextChangeLabel("movie.mp4.txt"))
        assertEquals("Binary file", nonTextChangeLabel("blob", "binary", AppLanguage.English))
    }
    @Test fun legacyMediaCountersDoNotPolluteMixedTotals() {
        val value = ChangedFilesResponse(listOf(ChangedFileDto("clip.mp4", 90000, 120, "added"),
            ChangedFileDto("main.py", 12, 3)), ChangedFilesTotalsDto(2, 90012, 123))
        assertEquals(12 to 3, textChangeCounts(value))
        assertEquals("2 files  +12 −3", changedFilesSummary(value, AppLanguage.English))
        assertEquals(listOf(DiffStat("+12", DiffTint.Positive), DiffStat("−3", DiffTint.Negative)), changedChipStats(value))
    }
    @Test fun mediaOnlySummaryShowsFileCountWithoutFakeZeroDiff() {
        val value = ChangedFilesResponse(listOf(ChangedFileDto("clip.mp4", 90000, 0, "added")), ChangedFilesTotalsDto(1, 90000, 0))
        assertEquals("1 file", changedFilesSummary(value, AppLanguage.English))
        assertEquals(1, changedChipStats(value).size)
        assertEquals(DiffTint.Neutral, changedChipStats(value).single().tint)
    }
    @Test fun reviewCountersExcludeMediaAndBinaryButKeepText() {
        val value = ChangesReview(listOf(ReviewFile("clip.mp4", additions = 90000, deletions = 4),
            ReviewFile("data", additions = 500, unavailableReason = "binary"),
            ReviewFile("main.py", additions = 8, deletions = 2)), ReviewTotals(3, 90508, 6))
        assertEquals(8 to 2, reviewTextCounts(value))
        assertTrue(isNonTextReview(value.files[0]))
        assertTrue(isNonTextReview(value.files[1]))
        assertFalse(isNonTextReview(value.files[2]))
    }
}
