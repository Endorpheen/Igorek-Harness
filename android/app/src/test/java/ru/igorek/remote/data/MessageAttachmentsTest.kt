package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MessageAttachmentsTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val upload = UploadAttachmentDto("a".repeat(32), "photo.jpg", 125, "image/jpeg", "image")
    private fun event(kind: String, text: String = "Look at this", attachments: Boolean = true) = BridgeEvent(
        kind = kind, sessionId = "chat", taskId = "task",
        payload = json.parseToJsonElement("""{"task_id":"task","message_id":"message","text":"$text",
            "attachments":${if (attachments) """[{"upload_id":"${upload.uploadId}","name":"photo.jpg","size":125,"mime_type":"image/jpeg","kind":"image"}]""" else "[]"}}""") as JsonObject,
    )

    @Test fun `live and queued user messages retain their uploaded images`() {
        for (kind in listOf("task.submitted", "task.message_added")) {
            val timeline = SessionTimeline("chat")
            timeline.apply(event(kind))
            val item = timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single()
            assertEquals(listOf(upload), item.attachments)
        }
    }

    @Test fun `image only message remains visible without text`() {
        val timeline = SessionTimeline("chat")
        timeline.apply(event("task.submitted", text = ""))
        assertEquals(listOf(upload), timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single().attachments)
    }

    @Test fun `history decodes attachment metadata and restores the thumbnail source`() {
        val message = json.decodeFromString<MessageDto>("""{"role":"user","text":"Look","task_id":"task","message_id":"message",
            "attachments":[{"upload_id":"${upload.uploadId}","name":"photo.jpg","size":125,"mime_type":"image/jpeg","kind":"image"}]}""")
        val timeline = SessionTimeline("chat")
        timeline.seedFromHistory(listOf(message))
        assertEquals(listOf(upload), timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single().attachments)
    }

    @Test fun `reconnected history hydrates an existing user message without duplicates`() {
        val timeline = SessionTimeline("chat")
        timeline.apply(event("task.submitted", attachments = false))
        timeline.seedFromHistory(listOf(MessageDto(role = "user", text = "Look at this", taskId = "task", messageId = "message", attachments = listOf(upload))))
        val items = timeline.snapshot().filterIsInstance<TimelineItem.UserText>()
        assertEquals(1, items.size)
        assertEquals(listOf(upload), items.single().attachments)
    }

    @Test fun `older stream metadata cannot erase a locally uploaded image`() {
        val timeline = SessionTimeline("chat")
        timeline.addLocal(TimelineItem.UserText("user-task-message", "Look", attachments = listOf(upload)))
        timeline.apply(event("task.submitted", attachments = false))
        assertEquals(listOf(upload), timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single().attachments)
    }

    @Test fun `invalid upload ids are not used as image download paths`() {
        val timeline = SessionTimeline("chat")
        val original = event("task.submitted")
        val payload = json.parseToJsonElement(original.payload.toString().replace(upload.uploadId, "../secret")) as JsonObject
        timeline.apply(original.copy(payload = payload))
        assertTrue(timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single().attachments.isEmpty())
    }

    @Test fun `old messages remain compatible without attachment fields`() {
        val message = json.decodeFromString<MessageDto>("""{"role":"user","text":"Old message"}""")
        assertTrue(message.attachments.isEmpty())
    }

    @Test fun `thumbnail messages hide matching legacy labels in both languages`() {
        for (label in listOf("вложения", "attachments")) {
            val text = "Look\n\n[$label: photo.jpg]"
            val item = TimelineItem.UserText("message", text, attachments = listOf(upload))
            assertEquals("Look", item.displayText)
            assertEquals(text, item.text)
            assertEquals("", item.copy(text = "[$label: photo.jpg]").displayText)
        }
    }

    @Test fun `legacy fallback and unrelated user prose are retained`() {
        val legacy = "Look\n\n[attachments: photo.jpg]"
        assertEquals(legacy, TimelineItem.UserText("message", legacy).displayText)
        for (text in listOf("[attachments: other.jpg]", "Explain [attachments: photo.jpg]", "[attachments: photo.jpg]\n\nKeep this")) {
            assertEquals(text, TimelineItem.UserText("message", text, attachments = listOf(upload)).displayText)
        }
    }

    @Test fun `multiple thumbnails replace only their complete matching label`() {
        val other = upload.copy(uploadId = "b".repeat(32), name = "second.png")
        val item = TimelineItem.UserText("message", "Look\n\n[attachments: photo.jpg, second.png]", attachments = listOf(upload, other))
        assertEquals("Look", item.displayText)
        assertEquals(item.text, item.copy(attachments = listOf(upload)).displayText)
    }
}
