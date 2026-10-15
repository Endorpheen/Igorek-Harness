package ru.igorek.remote.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.*
import org.junit.Test

class ScheduledResultTimelineTest {
    private val source = "chat-enews"
    private val messageId = "schedule-run-1"
    private val file = AttachmentDto("a".repeat(64), "post.jpg", 12, "image/jpeg")
    private fun delivery(seq: Long = 1, text: String = "Готовый черновик") = BridgeEvent(
        kind = "schedule.delivered", seq = seq, sessionId = source, taskId = "run-1",
        payload = buildJsonObject {
            put("message_id", messageId); put("text", text)
            put("files", JsonArray(listOf(buildJsonObject {
                put("id", file.id); put("name", file.name); put("size", file.size); put("mime_type", file.mimeType)
            })))
        },
    )
    private fun history() = MessageDto(role = "assistant", text = "Готовый черновик", messageId = messageId, files = listOf(file))

    @Test fun `live result stays in source thread with its downloadable file`() {
        val original = SessionTimeline(source); val work = SessionTimeline("chat-worker")
        original.apply(delivery()); work.apply(delivery())
        val row = original.snapshot().single() as TimelineItem.AssistantText
        assertEquals("Готовый черновик", row.text); assertEquals(listOf(file), row.files)
        assertTrue(work.snapshot().isEmpty())
    }
    @Test fun `history and replay use one stable message identity`() {
        val timeline = SessionTimeline(source)
        timeline.seedFromHistory(listOf(history()))
        timeline.apply(delivery()); timeline.apply(delivery()); timeline.apply(delivery(2))
        timeline.seedFromHistory(listOf(history()))
        assertEquals(1, timeline.snapshot().size)
        assertEquals(listOf(file), (timeline.snapshot().single() as TimelineItem.AssistantText).files)
    }
    @Test fun `missed live delivery is merged into an already cached history`() {
        val first = MessageDto(role="user", text="Ищи новости", taskId="task-user", messageId="user-1")
        val next = MessageDto(role="user", text="Спасибо", taskId="task-next", messageId="user-2")
        val timeline = SessionTimeline(source)
        timeline.seedFromHistory(listOf(first, next))
        timeline.seedFromHistory(listOf(first, history(), next))
        timeline.seedFromHistory(listOf(first, history(), next))
        assertEquals(listOf("Ищи новости", "Готовый черновик", "Спасибо"), timeline.snapshot().map {
            when(it) { is TimelineItem.UserText -> it.text; is TimelineItem.AssistantText -> it.text; else -> "" }
        })
    }
    @Test fun `incoming background result does not erase a foreground stream`() {
        val timeline = SessionTimeline(source)
        timeline.apply(BridgeEvent(kind="model.text_delta",seq=1,sessionId=source,taskId="foreground",payload=buildJsonObject { put("text","Пишу ответ") }))
        timeline.apply(delivery(2))
        assertEquals(2, timeline.snapshot().size)
        assertTrue((timeline.snapshot().first() as TimelineItem.AssistantText).streaming)
        assertEquals(listOf(file), (timeline.snapshot().last() as TimelineItem.AssistantText).files)
    }
    @Test fun `imported history retains its existing identity`() {
        val timeline = SessionTimeline(source)
        timeline.seedFromHistory(listOf(MessageDto(role="user",text="Old")))
        val imported = history().copy(historyId="imported-message")
        timeline.seedFromHistory(listOf(imported)); timeline.seedFromHistory(listOf(imported))
        assertEquals(1, timeline.snapshot().filterIsInstance<TimelineItem.AssistantText>().size)
        assertEquals("imported-message", timeline.snapshot().first().id)
    }
    @Test fun `file only result is shown and malformed empty event is ignored`() {
        val timeline = SessionTimeline(source);timeline.apply(delivery(text=""))
        assertEquals(listOf(file), (timeline.snapshot().single() as TimelineItem.AssistantText).files)
        timeline.apply(BridgeEvent(kind="schedule.delivered",seq=2,sessionId=source,payload=JsonObject(mapOf("text" to JsonPrimitive("ignored")))))
        assertEquals(1, timeline.snapshot().size)
    }
}
