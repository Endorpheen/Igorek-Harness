package ru.igorek.remote.ui.markdown

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

internal const val WEB_LINK_TAG = "web-url"

internal object MarkdownLinks {
    private val address = Regex("""https?://[^\s<>"`]+""", RegexOption.IGNORE_CASE)

    fun webUrl(raw: String): String? = raw.trim().toHttpUrlOrNull()?.toString()

    fun ranges(text: String): List<IntRange> = address.findAll(text).mapNotNull { match ->
        var end = match.range.last + 1
        while (end > match.range.first) {
            val candidate = text.substring(match.range.first, end)
            val last = candidate.last()
            val opening = when (last) { ')' -> '('; ']' -> '['; '}' -> '{'; else -> null }
            if (last in ".,;:!?…。，" || opening != null && candidate.count { it == last } > candidate.count { it == opening }) {
                end--
            } else break
        }
        val value = text.substring(match.range.first, end)
        if (webUrl(value) != null) match.range.first until end else null
    }.toList()
}
