package ru.igorek.remote.ui

import org.junit.Assert.*
import org.junit.Test
import ru.igorek.remote.data.*

class SpreadsheetChangesTest {
    @Test fun workbookFormatsHaveLabelsWithoutInventedCellCounts() {
        for (extension in listOf("xlsx", "xls", "xlsm", "xlsb", "xlt", "xltx", "xltm", "ods", "ots", "numbers")) {
            assertEquals(extension, "Таблица", nonTextChangeLabel("отчёт.$extension", language = AppLanguage.Russian))
            assertEquals(extension, "Spreadsheet", nonTextChangeLabel("REPORT.${extension.uppercase()}", language = AppLanguage.English))
        }
    }

    @Test fun textFormatsAndFinalExtensionsKeepLineCounts() {
        for (path in listOf("data.csv", "data.tsv", "script.py", "notes.md", "report.xlsx.py", "directory.xlsx/file.md")) {
            assertNull(path, nonTextChangeLabel(path))
        }
    }

    @Test fun mixedLegacyCountersExcludeBothWorkbookPathsInTheScreenshot() {
        val files = listOf(
            ChangedFileDto("fitness/charts/weight.png", 0, 0),
            ChangedFileDto("fitness/log.md", 1, 0),
            ChangedFileDto("fitness/update_xlsx.py", 2, 0),
            ChangedFileDto("fitness/weight_calories.xlsx", 2590, 2482),
            ChangedFileDto("fitness/weight_log.md", 1, 0),
            ChangedFileDto("weight_calories.xlsx", 2567, 2465),
        )
        val changes = ChangedFilesResponse(files, ChangedFilesTotalsDto(6, 5161, 4947))
        assertEquals(4 to 0, textChangeCounts(changes))
        assertEquals("6 files  +4 −0", changedFilesSummary(changes, AppLanguage.English))
        assertEquals(listOf(DiffStat("+4", DiffTint.Positive), DiffStat("−0", DiffTint.Negative)), changedChipStats(changes))
    }

    @Test fun workbookOnlySummaryShowsFileCount() {
        val changes = ChangedFilesResponse(listOf(ChangedFileDto("book.xlsx", 2590, 2482)), ChangedFilesTotalsDto(1, 2590, 2482))
        assertEquals("1 file", changedFilesSummary(changes, AppLanguage.English))
        assertEquals(0 to 0, textChangeCounts(changes))
        assertEquals(DiffTint.Neutral, changedChipStats(changes).single().tint)
    }

    @Test fun changedWorkbookRemainsVisibleWithZeroLineCounts() {
        val changes = ChangedFilesResponse(listOf(ChangedFileDto("book.xlsx", 0, 0)), ChangedFilesTotalsDto(1, 0, 0))
        assertTrue(sessionChangesVisible(changes))
        assertEquals(1, changedFilesCount(changes))
    }

    @Test fun reviewTotalsAlsoExcludeLegacyWorkbookCounts() {
        val review = ChangesReview(listOf(ReviewFile("book.xlsx", additions = 9000, deletions = 8000), ReviewFile("update.py", additions = 2, deletions = 1)), ReviewTotals(2, 9002, 8001))
        assertEquals(2 to 1, reviewTextCounts(review))
        assertTrue(isNonTextReview(review.files[0]))
        assertFalse(isNonTextReview(review.files[1]))
    }
}
