package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test
import kotlinx.serialization.json.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.decodeFromString

class NotificationReplayTest {
    @Test fun recoveredObservationCannotBeResurrectedByTheSameEvent() {
        val error = ErrorObservation(eventId = "original-event", code = "NET.CONNECTION_RESET", owner = "fixture")
        val first = mergeNotification(emptyList(), "Connection interrupted", "CONNECTION", 100, error)
        val recovered = recoverNotifications(first, "fixture", ErrorCatalog.stableAfterMs, 6000)
        assertEquals(recovered, mergeNotification(recovered, "Connection interrupted", "CONNECTION", 7000, error))
        assertEquals(0, unreadNotificationCount(mergeNotification(recovered, "Connection interrupted", "CONNECTION", 7000, error)))
    }

    @Test fun repeatedPresentationCannotReopenARecoveredLocalIncident() {
        val first = mergeUiNotification(emptyList(), "Fixture failed", "dialog", 100)
        val recovered = recoverNotifications(first, "ui:dialog", ErrorCatalog.stableAfterMs, 6000)
        assertEquals(recovered, mergeUiNotification(recovered, "Fixture failed", "dialog", 7000))
    }
    private fun event(kind: String = "task.error", seq: Long? = 10, task: String = "task-fixture",
        diagnostic: ErrorObservation? = null, status: String = "fail", reason: String = ""): BridgeEvent =
        BridgeEvent(kind = kind, seq = seq, ts = 1234, sessionId = "session-a", taskId = task, payload = buildJsonObject {
            put("status", status); if (reason.isNotBlank()) put("reason", reason)
            diagnostic?.let { put("diagnostic", Json.parseToJsonElement(Json.encodeToString(it))) }
        })

    @Test fun replayAndAlreadyProcessedEventsDoNotProduceNotifications() {
        assertFalse(shouldObserveNotificationEvent(event(seq = 9), 10, 0))
        assertFalse(shouldObserveNotificationEvent(event(seq = 10), 10, 0))
        assertFalse(shouldObserveNotificationEvent(event(seq = 10), 0, 10))
        assertTrue(shouldObserveNotificationEvent(event(seq = 11), 10, 10))
        assertTrue(shouldObserveNotificationEvent(event(seq = null), 10, 10))
    }

    @Test fun realNewFailureAfterRecoveryStillCreatesANewUnreadIncident() {
        val error = ErrorObservation(eventId = "first", code = "NET.CONNECTION_RESET", owner = "fixture")
        val first = mergeNotification(emptyList(), "Interrupted", "CONNECTION", 100, error)
        val recovered = recoverNotifications(first, "fixture", ErrorCatalog.stableAfterMs, 6000)
        val next = mergeNotification(recovered, "Interrupted", "CONNECTION", 7000, error.copy(eventId = "second"))
        assertEquals(2, next.size); assertEquals(1, unreadNotificationCount(next))
        assertEquals("second", next.last().diagnostic?.eventId)
    }

    @Test fun knownEventRemainsReadAndDoesNotChangeItsTimeOrCountAfterRestoration() {
        val error = ErrorObservation(eventId = "known", code = "NET.CONNECTION_RESET", owner = "fixture")
        val first = mergeNotification(emptyList(), "Interrupted", "CONNECTION", 100, error).map { it.copy(read = true) }
        val restored = Json.decodeFromString<List<NotificationEntry>>(Json.encodeToString(first))
        assertEquals(restored, mergeNotification(restored, "Interrupted", "CONNECTION", 9000, error))
        assertEquals(0, unreadNotificationCount(restored))
    }

    @Test fun onlyExactLegacyTimelinePresentationCopiesAreRemoved() {
        val old = NotificationEntry("Task failed", diagnostic = ErrorObservation(owner = "ui:timeline:session:finish-task-6338",
            operation = "display", endpointRole = "local_ui", phase = "presentation"))
        val real = old.copy(id = "real", diagnostic = old.diagnostic!!.copy(component = "model", endpointRole = "runtime_to_provider"))
        val input = old.copy(id = "input", diagnostic = old.diagnostic!!.copy(owner = "ui:session-input:a"))
        assertEquals(listOf(real, input), listOf(old, real, input).filterNot(::isTimelinePresentationNotification))
    }

    @Test fun renderingLegacyHistoryCannotCreateLocalUiNotifications() {
        assertTrue(mergeUiNotification(emptyList(), "Task failed", "timeline:session:finish-task-6338", 100).isEmpty())
        assertEquals(1, mergeUiNotification(emptyList(), "Upload failed", "session-input:a", 100).size)
    }

    @Test fun liveTypedFailureRetainsItsCodeReferencesAndSourceTime() {
        val typed = ErrorObservation(eventId = "typed", observedAt = 4567, code = "HTTP.SERVICE_UNAVAILABLE",
            component = "model", httpStatus = 500, owner = "model:fixture", httpRequestId = "req-fixture")
        val failure = notificationEventFailure(event(diagnostic = typed), emptyList())!!
        assertEquals("HTTP.SERVICE_UNAVAILABLE", failure.code)
        assertEquals(500, failure.httpStatus); assertEquals(4567L, failure.observedAt)
        assertEquals("task-fixture", failure.taskId); assertEquals("session-a", failure.sessionId)
        assertEquals("req-fixture", failure.httpRequestId)
    }

    @Test fun untypedTaskFailureIsUnknownRatherThanInventedInternalUiFailure() {
        val failure = notificationEventFailure(event(), emptyList())!!
        assertEquals("ERROR.UNKNOWN", failure.code); assertEquals("task", failure.component)
        assertEquals("bridge_to_mobile", failure.endpointRole); assertEquals(1234L, failure.observedAt)
        assertEquals(failure.eventId, notificationEventFailure(event(), emptyList())!!.eventId)
    }

    @Test fun statusAndFinishDoNotDuplicateTheExistingTaskFailure() {
        val diagnostic = notificationEventFailure(event(), emptyList())!!
        val known = mergeNotification(emptyList(), "Task failed", "OPERATION", 1234, diagnostic)
        assertNull(notificationEventFailure(event(kind = "task.finish", seq = 11), known))
        assertNull(notificationEventFailure(event(kind = "task.status", seq = 12), emptyList()))
        assertNotNull(notificationEventFailure(event(kind = "task.finish", seq = 13, task = "another-task"), known))
    }

    @Test fun successfulCancelledAndApprovalPausedTasksDoNotBecomeErrors() {
        assertNull(notificationEventFailure(event(kind = "task.finish", status = "ok"), emptyList()))
        assertNull(notificationEventFailure(event(kind = "task.finish", reason = "interrupted"), emptyList()))
        assertNull(notificationEventFailure(event(kind = "task.finish", reason = "approval_required"), emptyList()))
        val blocked = event(kind = "task.finish").let { it.copy(payload = JsonObject(it.payload + ("approval_blocked_count" to JsonPrimitive(1)))) }
        assertNull(notificationEventFailure(blocked, emptyList()))
        assertNull(notificationEventFailure(event(kind = "model.text_delta"), emptyList()))
    }

    @Test fun newToolAndCompactionFailuresAreRetainedWithoutRenderingSideEffects() {
        assertNotNull(notificationEventFailure(event(kind = "tool.result", status = "error"), emptyList()))
        assertNull(notificationEventFailure(event(kind = "tool.result", status = "done"), emptyList()))
        assertNotNull(notificationEventFailure(event(kind = "compaction.failed"), emptyList()))
    }

    @Test fun aReadIncidentStaysReadWhenTheScreenRevisitsItsSameSource() {
        val first = mergeUiNotification(emptyList(), "Upload failed", "session-input:a", 100).map { it.copy(read = true) }
        repeat(10) { assertEquals(first, mergeUiNotification(first, "Upload failed", "session-input:a", 9000 + it.toLong())) }
        assertEquals(0, unreadNotificationCount(first))
    }

    @Test fun recoveredTransientFailureCannotHideALaterTerminalFailureInTheSameTask() {
        val error = notificationEventFailure(event(), emptyList())!!
        val original = mergeNotification(emptyList(), "Task failure", "OPERATION", 100, error)
        val recovered = recoverNotifications(original, error.owner, ErrorCatalog.stableAfterMs, 6000)
        val later = notificationEventFailure(event(kind = "task.finish", seq = 20), recovered)
        assertNotNull(later)
        assertNotEquals(error.eventId, later!!.eventId)
        val result = mergeNotification(recovered, "New task failure", "OPERATION", 7000, later)
        assertEquals(1, unreadNotificationCount(result))
    }

}
