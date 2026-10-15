package ru.igorek.remote.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import java.net.URLEncoder

@Serializable
internal data class ReviewFile(
    val path: String = "", val status: String = "modified",
    val additions: Int? = null, val deletions: Int? = null,
    val source: String = "unavailable", val note: String = "",
    @SerialName("old_path") val oldPath: String = "",
    val hunks: List<DiffHunkDto> = emptyList(), val truncated: Boolean = false,
) {
    fun diff() = FileDiffResponse(path, additions ?: 0, deletions ?: 0, source, truncated, hunks = hunks)
}

@Serializable
internal data class ReviewTotals(
    val files: Int = 0, val additions: Int = 0, val deletions: Int = 0,
    val unknown: Int = 0, val partial: Boolean = false,
)

@Serializable
internal data class ChangesReview(
    val files: List<ReviewFile> = emptyList(), val totals: ReviewTotals = ReviewTotals(),
    val mode: String = "session", val revision: String = "", val base: String = "",
    @SerialName("base_label") val baseLabel: String = "",
    val refs: List<String> = emptyList(), val warning: String = "", val omitted: Int = 0,
    @SerialName("excluded_untracked") val excludedUntracked: Int = 0,
)

internal fun reviewQuery(mode: String, base: String, untracked: Boolean, path: String = "", revision: String = ""): String =
    listOf("mode" to mode, "base" to base, "include_untracked" to if (untracked) "1" else "0", "path" to path, "revision" to revision)
        .joinToString("&") { (key, value) -> "$key=${URLEncoder.encode(value, "UTF-8").replace("+", "%20")}" }

internal fun reviewFiles(files: List<ReviewFile>, query: String, status: String): List<ReviewFile> =
    files.filter { it.path.contains(query, ignoreCase = true) && (status.isEmpty() || it.status == status) }.sortedBy { it.path }
