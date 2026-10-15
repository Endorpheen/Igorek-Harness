package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class ChangesReviewTest {
    @Test fun unknownLineCountsStayUnknownAndPartialTotalsSurvive() {
        val review = Json { ignoreUnknownKeys = true }.decodeFromString<ChangesReview>("""{"files":[{"path":"image.png","additions":null,"deletions":null,"source":"unavailable","note":"binary"}],"totals":{"files":1,"unknown":1,"partial":true},"scope":"tracked"}""")
        assertNull(review.files.single().additions)
        assertTrue(review.totals.partial)
        assertEquals("unavailable", review.files.single().diff().source)
    }
    @Test fun queryPinsRevisionAndEscapesBranchAndPath() {
        val query = reviewQuery("branch", "feature/a & b", true, "src/a+b.kt", "abc123")
        assertTrue(query.contains("base=feature%2Fa%20%26%20b"))
        assertTrue(query.contains("path=src%2Fa%2Bb.kt"))
        assertTrue(query.contains("revision=abc123"))
        assertTrue(query.contains("include_untracked=1"))
    }
    @Test fun navigationUsesFilteredFilesWithoutChangingTotals() {
        val files = listOf(ReviewFile("src/B.kt", "deleted"), ReviewFile("src/a.kt", "modified"), ReviewFile("doc/a.md", "modified"))
        assertEquals(listOf("src/a.kt"), reviewFiles(files, "SRC/", "modified").map { it.path })
        assertEquals(3, files.size)
    }
}
