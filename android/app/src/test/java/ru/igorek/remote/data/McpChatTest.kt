package ru.igorek.remote.data

import org.junit.Assert.assertEquals
import org.junit.Test

class McpChatTest {
    @Test fun privateRepliesAppearInMainFeedWithoutChangingRegularHistory() {
        val regular = listOf(TimelineItem.UserText("ordinary", "hello"))
        val mcp = listOf(McpChatRow(1, TimelineItem.UserText("mail-user", "read mail")),
            McpChatRow(1, TimelineItem.AssistantText("mail-answer", "private summary")))
        val merged = mergeMcpChatRows(regular, mcp)
        assertEquals(listOf("ordinary", "mail-user", "mail-answer"), merged.map { it.id })
        assertEquals(listOf("ordinary"), regular.map { it.id })
        val later = regular + TimelineItem.AssistantText("ordinary-answer", "hello back")
        assertEquals(listOf("ordinary", "mail-user", "mail-answer", "ordinary-answer"), mergeMcpChatRows(later,mcp).map { it.id })
    }

    @Test fun historyReloadKeepsPrivateRowsAtValidFeedPositions() {
        val rows = listOf(McpChatRow(10,TimelineItem.AssistantText("mail", "summary")))
        assertEquals(listOf("mail"),mergeMcpChatRows(emptyList(),rows).map { it.id })
        assertEquals(emptyList<TimelineItem>(),mergeMcpChatRows(emptyList(),emptyList()))
    }
    @Test fun completedMcpReplySurvivesScreenAndHistoryStoreRecreation() {
        val storage = mutableMapOf<String, String>()
        fun history() = McpChatHistory({ storage[it].orEmpty() }, { session, encoded -> storage[session] = encoded })
        val rows = listOf(McpChatRow(1, TimelineItem.UserText("request", "Последние письма?")),
            McpChatRow(1, TimelineItem.AssistantText("answer", "Отправитель: пример\nТема: проверка")))
        history().save("session-a", rows)
        history().save("session-b", listOf(McpChatRow(0, TimelineItem.AssistantText("other", "Другой ответ"))))
        assertEquals(rows, history().load("session-a"))
        assertEquals(listOf("other"), history().load("session-b").map { it.item.id })
        assertEquals(emptyList<McpChatRow>(), history().load("session-c"))
        val ordinary = listOf(TimelineItem.UserText("ordinary", "hello"))
        assertEquals(listOf("ordinary", "request", "answer"), mergeMcpChatRows(ordinary, history().load("session-a")).map { it.id })
        assertEquals(listOf("ordinary"), ordinary.map { it.id })
    }

    @Test fun interruptedCallRestoresAsStoppedAndKeepsEarlierCompletedReply() {
        val storage = mutableMapOf<String, String>()
        val history = McpChatHistory({ storage[it].orEmpty() }, { session, encoded -> storage[session] = encoded })
        history.save("session", listOf(
            McpChatRow(0, TimelineItem.AssistantText("previous", "Сохранённый ответ")),
            McpChatRow(0, TimelineItem.Status("pending", "Чтение писем"), inProgress = true),
            McpChatRow(0, TimelineItem.Status("error", "Доступ отозван", level = "error")),
        ))
        val restored = history.load("session")
        assertEquals("Сохранённый ответ", (restored[0].item as TimelineItem.AssistantText).text)
        assertEquals("Вызов MCP прерван", (restored[1].item as TimelineItem.Status).text)
        assertEquals(false, restored[1].inProgress)
        assertEquals("Доступ отозван", (restored[2].item as TimelineItem.Status).text)
        assertEquals("error", (restored[2].item as TimelineItem.Status).level)
    }

    @Test fun legacyHistoryProvidesTitleAndPreviewWithoutAnotherRequest() {
        val encoded = """[{"position":0,"id":"u","kind":"user","text":"Покажи последние пять писем"},{"position":0,"id":"a","kind":"assistant","text":"Google: оповещение безопасности"}]"""
        val history = McpChatHistory({ encoded }, { _, _ -> error("Read only") })
        val summary = withMcpSessionSummary(SessionSummary("chat-20261007", title = "chat-20261007"), history.load("chat-20261007"))
        assertEquals("MCP · Покажи последние пять писем", summary.title)
        assertEquals("Google: оповещение безопасности", summary.mcpPreview)
        assertEquals(true, summary.generatedMcpTitle)
    }

    @Test fun serverSurvivesStorageAndLatestReplyAppearsUnderFirstRequest() {
        var encoded = ""
        val history = McpChatHistory({ encoded }, { _, value -> encoded = value })
        val rows = listOf(
            McpChatRow(0, TimelineItem.UserText("u", "Покажи  последние\nпять писем"), server = "gmail"),
            McpChatRow(0, TimelineItem.AssistantText("a", "Первый ответ"), server = "gmail"),
            McpChatRow(0, TimelineItem.UserText("u2", "А что во втором?"), server = "gmail"),
            McpChatRow(0, TimelineItem.AssistantText("a2", "Уведомление о подписке"), server = "gmail"),
            McpChatRow(0, TimelineItem.Status("progress", "Работаю…"), server = "gmail"),
        )
        history.save("s", rows)
        assertEquals(rows, history.load("s"))
        val summary = withMcpSessionSummary(SessionSummary("s"), history.load("s"))
        assertEquals("Gmail · Покажи последние пять писем", summary.title)
        assertEquals("Уведомление о подписке", summary.mcpPreview)
        assertEquals(summary, withMcpSessionSummary(summary, rows))
    }

    @Test fun manualAndOrdinaryTitlesArePreservedWhilePreviewIsAvailable() {
        val rows = listOf(McpChatRow(0, TimelineItem.UserText("u", "Почта?")),
            McpChatRow(0, TimelineItem.AssistantText("a", "Пять писем")))
        val session = SessionSummary("s", title = "Моя переписка")
        val summary = withMcpSessionSummary(session, rows)
        assertEquals("Моя переписка", summary.title)
        assertEquals(false, summary.generatedMcpTitle)
        assertEquals("Пять писем", summary.mcpPreview)
        assertEquals(session, withMcpSessionSummary(session, emptyList()))
        assertEquals("Моя переписка", withMcpSessionSummary(summary, rows).title)
    }

    @Test fun summaryDoesNotInventARequestFromStatusOrAnswer() {
        val session = SessionSummary("s")
        val summary = withMcpSessionSummary(session, listOf(
            McpChatRow(0, TimelineItem.Status("p", "Чтение Gmail")),
            McpChatRow(0, TimelineItem.AssistantText("a", "Ответ")),
        ))
        assertEquals("", summary.title)
        assertEquals(false, summary.generatedMcpTitle)
        assertEquals("Ответ", summary.mcpPreview)
    }

    @Test fun longSummariesAreBoundedAndNotSerializedIntoServerDtos() {
        val summary = withMcpSessionSummary(SessionSummary("s"), listOf(
            McpChatRow(0, TimelineItem.UserText("u", "я".repeat(400)), server = "gmail"),
            McpChatRow(0, TimelineItem.AssistantText("a", "с".repeat(400)), server = "gmail"),
        ))
        assertEquals(88, summary.title.length)
        assertEquals(180, summary.mcpPreview.length)
        val encoded = kotlinx.serialization.json.Json.encodeToString(SessionSummary.serializer(), summary)
        org.junit.Assert.assertFalse(encoded.contains("mcpPreview"))
        org.junit.Assert.assertFalse(encoded.contains("generatedMcpTitle"))
    }
}
