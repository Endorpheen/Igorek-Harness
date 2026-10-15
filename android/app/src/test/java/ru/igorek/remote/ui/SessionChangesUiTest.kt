package ru.igorek.remote.ui

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.ChangedFileDto
import ru.igorek.remote.data.ChangedFilesResponse
import ru.igorek.remote.data.ChangedFilesTotalsDto
import ru.igorek.remote.data.DiffHunkDto
import ru.igorek.remote.data.DiffLineDto
import ru.igorek.remote.data.FileDiffResponse

class SessionChangesUiTest {
    private val threeFiles = ChangedFilesResponse(
        files = listOf(
            ChangedFileDto("factory/core/x.py", 12, 3, "modified"),
            ChangedFileDto("factory/core/new.py", 30, 0, "added"),
            ChangedFileDto("factory/core/old.py", 0, 4, "deleted"),
        ),
        totals = ChangedFilesTotalsDto(files = 3, additions = 42, deletions = 7),
    )

    @Test
    fun `chip content description keeps pluralised file count and diff totals`() {
        assertEquals("3 файла  +42 −7", changedFilesSummary(threeFiles, AppLanguage.Russian))
        assertEquals("3 files  +42 −7", changedFilesSummary(threeFiles, AppLanguage.English))
    }

    @Test
    fun `chip label shows only diff totals without file count`() {
        assertEquals(
            listOf(DiffStat("+42", DiffTint.Positive), DiffStat("−7", DiffTint.Negative)),
            changedChipStats(threeFiles),
        )
    }

    @Test
    fun `russian file count keeps plural forms`() {
        assertEquals("1 файл", changedFileCountLabel(1, AppLanguage.Russian))
        assertEquals("2 файла", changedFileCountLabel(2, AppLanguage.Russian))
        assertEquals("5 файлов", changedFileCountLabel(5, AppLanguage.Russian))
        assertEquals("11 файлов", changedFileCountLabel(11, AppLanguage.Russian))
        assertEquals("21 файл", changedFileCountLabel(21, AppLanguage.Russian))
        assertEquals("22 файла", changedFileCountLabel(22, AppLanguage.Russian))
        assertEquals("101 файл", changedFileCountLabel(101, AppLanguage.Russian))
    }

    @Test
    fun `english file count keeps plural forms`() {
        assertEquals("0 files", changedFileCountLabel(0, AppLanguage.English))
        assertEquals("1 file", changedFileCountLabel(1, AppLanguage.English))
        assertEquals("2 files", changedFileCountLabel(2, AppLanguage.English))
    }

    @Test
    fun `additions map to green and deletions map to red`() {
        assertEquals(DiffStat("+12", DiffTint.Positive), additionStat(12))
        assertEquals(DiffStat("−3", DiffTint.Negative), deletionStat(3))
        assertEquals(Color(0xFF44D7A8), diffStatColor(additionStat(1).tint))
        assertEquals(Color(0xFFFF667A), diffStatColor(deletionStat(1).tint))
        assertEquals(Color(0xFF5EA7FF), diffStatColor(DiffTint.Neutral))
    }

    @Test
    fun `file status maps to label tint and icon`() {
        assertEquals("добавлен", changeStatusLabel("added", AppLanguage.Russian))
        assertEquals("удалён", changeStatusLabel("deleted", AppLanguage.Russian))
        assertEquals("изменён", changeStatusLabel("modified", AppLanguage.Russian))
        assertEquals("added", changeStatusLabel(" ADDED ", AppLanguage.English))
        assertEquals("renamed", changeStatusLabel("renamed", AppLanguage.English))

        assertEquals(DiffTint.Positive, changeStatusTint("added"))
        assertEquals(DiffTint.Negative, changeStatusTint("deleted"))
        assertEquals(DiffTint.Neutral, changeStatusTint("modified"))
        assertEquals(DiffTint.Neutral, changeStatusTint("renamed"))
    }

    @Test
    fun `empty changes stay hidden`() {
        assertFalse(sessionChangesVisible(null))
        assertFalse(sessionChangesVisible(ChangedFilesResponse()))
        assertFalse(
            sessionChangesVisible(
                ChangedFilesResponse(totals = ChangedFilesTotalsDto(files = 0, additions = 0, deletions = 0))
            )
        )
    }

    @Test
    fun `changes are visible when files or totals are present`() {
        assertTrue(sessionChangesVisible(threeFiles))
        assertTrue(
            sessionChangesVisible(ChangedFilesResponse(totals = ChangedFilesTotalsDto(files = 1, additions = 5, deletions = 0)))
        )
        assertTrue(
            sessionChangesVisible(
                ChangedFilesResponse(files = listOf(ChangedFileDto("a.py", 0, 0, "modified")))
            )
        )
    }

    @Test
    fun `file count prefers totals and falls back to the list size`() {
        assertEquals(3, changedFilesCount(threeFiles))
        assertEquals(
            5,
            changedFilesCount(
                ChangedFilesResponse(
                    files = listOf(ChangedFileDto("a.py"), ChangedFileDto("b.py")),
                    totals = ChangedFilesTotalsDto(files = 5),
                )
            )
        )
        assertEquals(
            2,
            changedFilesCount(
                ChangedFilesResponse(
                    files = listOf(ChangedFileDto("a.py"), ChangedFileDto("b.py")),
                    totals = ChangedFilesTotalsDto(files = 0, additions = 0, deletions = 0),
                )
            )
        )
    }

    @Test
    fun `diff line type maps to a tint`() {
        assertEquals(DiffTint.Positive, diffLineTint("+"))
        assertEquals(DiffTint.Negative, diffLineTint("-"))
        assertEquals(DiffTint.Neutral, diffLineTint(" "))
        assertEquals(DiffTint.Neutral, diffLineTint(""))
        assertEquals(DiffTint.Neutral, diffLineTint("?"))
        assertEquals(DiffTint.Positive, diffLineTint(" + "))
    }

    @Test
    fun `unavailable and empty diffs read as unavailable`() {
        val unavailable = FileDiffResponse(path = "a.py", source = "unavailable")
        assertTrue(fileDiffUnavailable(unavailable))
        assertTrue(fileDiffUnavailable(FileDiffResponse(path = "a.py", source = "checkpoint")))
        assertFalse(fileDiffUnavailable(null))
        assertEquals("diff недоступен", fileDiffNotice(unavailable, AppLanguage.Russian))
        assertEquals("diff unavailable", fileDiffNotice(unavailable, AppLanguage.English))

        val checkpoint = FileDiffResponse(
            path = "a.py",
            source = "checkpoint",
            hunks = listOf(DiffHunkDto(header = "@@ -1 +1 @@", lines = listOf(DiffLineDto("+", "x")))),
        )
        assertFalse(fileDiffUnavailable(checkpoint))
        assertNull(fileDiffNotice(checkpoint, AppLanguage.Russian))
    }

    @Test
    fun `truncated diff keeps content and shows a truncation notice`() {
        val diff = FileDiffResponse(
            path = "a.py",
            source = "checkpoint",
            truncated = true,
            hunks = listOf(DiffHunkDto(header = "@@ -1 +1 @@", lines = listOf(DiffLineDto(" ", "ctx")))),
        )

        assertFalse(fileDiffUnavailable(diff))
        assertTrue(fileDiffNotice(diff, AppLanguage.Russian)!!.contains("обрезан"))
        assertTrue(fileDiffNotice(diff, AppLanguage.English)!!.contains("truncated"))
    }

    @Test
    fun `diff source is labelled for the header`() {
        assertEquals("чекпоинт", diffSourceLabel("checkpoint", AppLanguage.Russian))
        assertEquals("checkpoint", diffSourceLabel("checkpoint", AppLanguage.English))
        assertEquals("недоступен", diffSourceLabel("unavailable", AppLanguage.Russian))
        assertEquals("custom", diffSourceLabel("custom", AppLanguage.English))
    }

    @Test
    fun `diff copy text rebuilds hunk headers markers and lines exactly`() {
        val diff = FileDiffResponse(
            path = "a.py",
            source = "checkpoint",
            hunks = listOf(
                DiffHunkDto(
                    header = "@@ -1,2 +1,2 @@",
                    lines = listOf(DiffLineDto(" ", "ctx"), DiffLineDto("-", "old"), DiffLineDto("+", "new")),
                )
            ),
        )

        assertEquals("@@ -1,2 +1,2 @@\n ctx\n-old\n+new", diffCopyText(diff))
    }

    @Test
    fun `diff copy text skips blank headers and keeps every hunk`() {
        val diff = FileDiffResponse(
            path = "a.py",
            source = "checkpoint",
            hunks = listOf(
                DiffHunkDto(header = "", lines = listOf(DiffLineDto("+", "a"))),
                DiffHunkDto(header = "@@ -5 +5 @@", lines = listOf(DiffLineDto(" ", "b"), DiffLineDto("+", "c"))),
            ),
        )

        assertEquals("+a\n@@ -5 +5 @@\n b\n+c", diffCopyText(diff))
    }

    @Test
    fun `diff copy text keeps all received lines of a truncated diff`() {
        val diff = FileDiffResponse(
            path = "a.py",
            source = "checkpoint",
            truncated = true,
            hunks = listOf(DiffHunkDto(header = "@@ -1 +1 @@", lines = listOf(DiffLineDto("+", "kept")))),
        )

        assertEquals("@@ -1 +1 @@\n+kept", diffCopyText(diff))
    }

    @Test
    fun `diff copy text is empty for an empty diff`() {
        assertEquals("", diffCopyText(FileDiffResponse(path = "a.py", source = "unavailable")))
    }

    @Test
    fun `tapping a changed file asks the bridge for that path`() {
        val calls = mutableListOf<Pair<String, String>>()
        val opened = requestDiffFor(ChangedFileDto("factory/core/x.py", 4, 2, "modified"), "ses-1") { sessionId, path ->
            calls += sessionId to path
        }

        assertEquals("factory/core/x.py", opened)
        assertEquals(listOf("ses-1" to "factory/core/x.py"), calls)
    }
}
