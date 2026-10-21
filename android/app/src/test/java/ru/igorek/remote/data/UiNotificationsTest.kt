package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test

class UiNotificationsTest {
    @Test fun taskErrorsMoveToNotificationsWithoutReclassifyingMessageText() {
        assertEquals("Task failed\nHTTP 500", notificationFailureText(TimelineItem.Status("task", "Task failed", "error", "HTTP 500")))
        assertNull(notificationFailureText(TimelineItem.Status("done", "Done", "ok")))
        assertNull(notificationFailureText(TimelineItem.AssistantText("answer", "An error means...")))
        assertNull(notificationFailureText(TimelineItem.ToolCall("tool", "Read", "", "ok", "error is just file content")))
    }
    @Test fun repeatedRenderingDoesNotDuplicateAnIncidentAndOwnersStayDistinct() {
        val first = mergeUiNotification(emptyList(), "fixture failed", "dialog-a", 10)
        assertEquals(1, first.size)
        assertEquals(first, mergeUiNotification(first, "fixture failed", "dialog-a", 20))
        assertEquals(2, mergeUiNotification(first, "fixture failed", "dialog-b", 30).size)
    }
    @Test fun existingQuietTransportIncidentIsNotPromotedByUiRendering() {
        val diagnostic = ErrorObservation(code = "NET.READ_TIMEOUT", owner = "mobile-events", phase = "sse_read")
        val first = mergeNotification(emptyList(), "fixture timeout", "CONNECTION", 10, diagnostic, quiet = true)
        assertEquals(first, mergeUiNotification(first, "fixture timeout", "panel", 20))
        assertTrue(first.single().quiet)
    }
    @Test fun localErrorsAreSanitizedAndRetainedInNotificationHistory() {
        val result = mergeUiNotification(emptyList(), "failed token=private-secret", "attachment", 10)
        assertEquals(1, result.size)
        assertFalse(result.single().text.contains("private-secret"))
        assertEquals("local_ui", result.single().diagnostic?.endpointRole)
    }
}
