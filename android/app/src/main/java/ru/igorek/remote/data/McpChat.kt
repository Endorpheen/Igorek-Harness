package ru.igorek.remote.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

internal data class McpChatRow(val position: Int, val item: TimelineItem, val inProgress: Boolean = false, val server: String = "")

@Serializable
private data class StoredMcpRow(val position: Int, val id: String, val kind: String, val text: String,
    val level: String = "info", val inProgress: Boolean = false, val server: String = "")

internal class McpChatHistory(private val read: (String) -> String, private val write: (String, String) -> Unit) {
    fun load(sessionId: String): List<McpChatRow> {
        val encoded = read(sessionId)
        if (encoded.isBlank()) return emptyList()
        return Json.decodeFromString<List<StoredMcpRow>>(encoded).map { row ->
            val item = when (row.kind) {
                "user" -> TimelineItem.UserText(row.id, row.text)
                "assistant" -> TimelineItem.AssistantText(row.id, row.text)
                "status" -> TimelineItem.Status(row.id, if (row.inProgress) "Вызов MCP прерван" else row.text, row.level)
                else -> error("Unsupported MCP history row")
            }
            McpChatRow(row.position, item, server = row.server)
        }
    }

    fun save(sessionId: String, rows: List<McpChatRow>) {
        val stored = rows.map { row ->
            when (val item = row.item) {
                is TimelineItem.UserText -> StoredMcpRow(row.position, item.id, "user", item.text, server = row.server)
                is TimelineItem.AssistantText -> StoredMcpRow(row.position, item.id, "assistant", item.text, server = row.server)
                is TimelineItem.Status -> StoredMcpRow(row.position, item.id, "status", item.text, item.level, row.inProgress, row.server)
                else -> error("Unsupported MCP history row")
            }
        }
        write(sessionId, Json.encodeToString(stored))
    }
}

internal fun mergeMcpChatRows(regular: List<TimelineItem>, mcp: List<McpChatRow>): List<TimelineItem> {
    val grouped = mcp.groupBy { it.position.coerceIn(0, regular.size) }
    return buildList {
        for (index in 0..regular.size) {
            grouped[index]?.forEach { add(it.item) }
            if (index < regular.size) add(regular[index])
        }
    }
}

internal fun withMcpSessionSummary(session: SessionSummary, rows: List<McpChatRow>): SessionSummary {
    if (rows.isEmpty()) return session
    fun excerpt(text: String, limit: Int): String {
        val compact = text.trim().replace(Regex("\\s+"), " ")
        return if (compact.length <= limit) compact else compact.take(limit - 1).trimEnd() + "…"
    }
    val firstRequest = rows.firstOrNull { (it.item as? TimelineItem.UserText)?.text?.isNotBlank() == true }
    val request = (firstRequest?.item as? TimelineItem.UserText)?.text.orEmpty()
    val automatic = session.title.isBlank() || session.title == session.sessionId || session.generatedMcpTitle
    val connection = when (firstRequest?.server) {
        "gmail" -> "Gmail"
        null, "" -> "MCP"
        else -> firstRequest?.server.orEmpty()
    }
    val response = rows.mapNotNull { (it.item as? TimelineItem.AssistantText)?.text }
        .lastOrNull { it.isNotBlank() }.orEmpty()
    return session.copy(
        title = if (automatic && request.isNotBlank()) "$connection · ${excerpt(request, 80)}" else session.title,
        generatedMcpTitle = automatic && request.isNotBlank(),
        mcpPreview = excerpt(response, 180),
    )
}
