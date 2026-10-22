package ru.igorek.remote.data

import kotlinx.serialization.json.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.decodeFromString
import org.junit.Assert.*
import org.junit.Test

class ToolDiagnosticsTest {
    private val seconds = 1792629242L
    private val millis = seconds * 1000
    private fun event(seq: Long = 10, status: String = "error", input: String = """{"action":"files","service":"fixture","file_id":"wrong-field"}""",
        result: String = """{"error":"invalid_service_fields"}""", tool: String = "host_service", kind: String = "tool.result",
        task: String = "task-fixture", session: String = "session-fixture"): BridgeEvent = BridgeEvent(
        kind = kind, seq = seq, ts = seconds + seq - 10, taskId = task, sessionId = session, payload = buildJsonObject {
            put("status", status); put("tool_name", tool); put("tool_use_id", "call-$seq")
            put("tool_input", Json.parseToJsonElement(input)); put("result_preview", result)
        })
    private fun observe(event: BridgeEvent = event()): List<NotificationEntry> {
        val error = notificationEventFailure(event, emptyList())!!
        return mergeNotification(emptyList(), errorMessage(error), "OPERATION", millis + 2000, error)
    }

    @Test fun originalInvalidFieldsRetainsItsCauseAndCorrectUtcTime() {
        val entries = observe(); val error = entries.single().diagnostic!!
        assertEquals("TOOL.INVALID_ARGUMENTS", error.code)
        assertEquals("invalid_service_fields", error.reasonCode)
        assertEquals("host_service", error.toolName); assertEquals("fixture", error.service); assertEquals("files", error.toolAction)
        assertEquals(millis, error.observedAt); assertEquals(10L, error.sourceSequence)
        assertEquals("2026-10-22T00:34:02Z", java.time.Instant.ofEpochMilli(error.observedAt).toString())
        val report = incidentDiagnosticReport(entries.single(), "fixture", AppLanguage.Russian, millis + 3000)
        assertTrue(report.contains("invalid_service_fields")); assertTrue(report.contains("host_service"))
        assertTrue(report.contains("недопустимый набор полей")); assertFalse(report.contains("1970-"))
    }

    @Test fun correctingTheCallClosesItAndRemovesItFromBellAndList() {
        val first = observe().map { it.copy(read = true) }
        val success = event(seq = 11, status = "done", input = """{"service":"fixture","action":"files"}""", result = """{"files":[]}""")
        val result = reconcileNotificationEvent(first, success)
        assertEquals("recovered", result.single().state)
        assertEquals("tool_retry_succeeded", result.single().recoveryProof)
        assertEquals(first.single().id, result.single().id); assertTrue(result.single().read)
        assertEquals(1, result.single().count)
        assertTrue(visibleNotifications(result).isEmpty()); assertEquals(0, unreadNotificationCount(result))
        val report = incidentDiagnosticReport(result.single(), "fixture", AppLanguage.Russian, millis + 4000)
        assertTrue(report.contains("повторный вызов")); assertTrue(report.contains("invalid_service_fields"))
        assertEquals(result, reconcileNotificationEvent(result, success))
    }

    @Test fun completedTaskCanResolveRejectedParametersWithoutInventingASuccessfulRetry() {
        val result = reconcileNotificationEvent(observe(), event(seq = 12, status = "ok", kind = "task.finish"))
        assertEquals("recovered", result.single().state)
        assertEquals("task_completed_after_rejected_call", result.single().recoveryProof)
        assertTrue(notificationRecoveryExplanation(result.single().recoveryProof, AppLanguage.English).contains("was not executed"))
    }

    @Test fun taskCompletionDoesNotHideAnUnresolvedExecutionFailure() {
        val first = observe(event(result = """{"error":"file_not_found"}"""))
        assertEquals(first, reconcileNotificationEvent(first, event(seq = 12, kind = "task.finish", status = "ok")))
        assertEquals(1, visibleNotifications(first).size)
    }

    @Test fun anotherTaskSessionServiceOrEarlierSuccessDoesNotCloseTheFailure() {
        val first = observe()
        val success = event(seq = 11, status = "done", input = """{"action":"files","service":"fixture"}""")
        for (other in listOf(success.copy(taskId = "other"), success.copy(sessionId = "other"),
            event(seq = 11, status = "done", input = """{"action":"files","service":"different"}"""),
            success.copy(seq = 9), event(seq = 11, status = "done", input = """{"action":"export","service":"fixture"}"""))) {
            assertEquals(first, reconcileNotificationEvent(first, other))
        }
    }

    @Test fun executionFailuresRequireTheSameResourceAndInputs() {
        val failed = event(tool = "Read", input = """{"file_path":"/fixture/a","limit":20}""", result = """{"error":"file_not_found"}""")
        val first = observe(failed)
        assertEquals(first, reconcileNotificationEvent(first, event(seq = 11, status = "done", tool = "Read", input = """{"file_path":"/fixture/b","limit":20}""")))
        val result = reconcileNotificationEvent(first, event(seq = 12, status = "done", tool = "Read", input = """{"limit":20,"file_path":"/fixture/a"}"""))
        assertEquals("recovered", result.single().state)
    }

    @Test fun unknownOutcomeCannotBeHiddenByToolOrTaskSuccess() {
        val first = observe().map { it.copy(diagnostic = it.diagnostic!!.copy(outcome = "unknown")) }
        assertEquals(first, reconcileNotificationEvent(first, event(seq = 11, status = "done", input = """{"action":"files","service":"fixture"}""")))
        assertEquals(first, reconcileNotificationEvent(first, event(seq = 12, kind = "task.finish", status = "ok")))
    }

    @Test fun legacyUnknownIncidentIsEnrichedAndRecoveredFromHistoryWithoutANewCard() {
        val event = event(); val enriched = notificationEventFailure(event, emptyList())!!
        val legacy = enriched.copy(code = "ERROR.UNKNOWN", observedAt = seconds, component = "task", toolName = "", toolUseId = "",
            toolAction = "", service = "", reasonCode = "", operationKey = "", sourceSequence = null)
        val first = mergeNotification(emptyList(), "Unknown failure", "OPERATION", millis + 2000, legacy).map { it.copy(read = true) }
        val restored = Json.decodeFromString<List<NotificationEntry>>(Json.encodeToString(first))
        val updated = reconcileNotificationEvent(restored, event)
        assertEquals(1, updated.size); assertEquals(first.single().id, updated.single().id); assertTrue(updated.single().read)
        assertEquals("TOOL.INVALID_ARGUMENTS", updated.single().diagnostic!!.code)
        assertEquals(millis, updated.single().diagnostic!!.observedAt)
        val recovered = reconcileNotificationEvent(updated, event(seq = 11, status = "done", input = """{"action":"files","service":"fixture"}"""))
        assertTrue(visibleNotifications(recovered).isEmpty())
        assertEquals(recovered, reconcileNotificationEvent(recovered, event))
    }

    @Test fun legacyTimeRepairIsLimitedToKnownFallbackObservations() {
        val error = ErrorObservation(eventId = "event:legacy", observedAt = seconds, endpointRole = "bridge_to_mobile")
        val entry = NotificationEntry("Failure", diagnostic = error, events = listOf(error))
        assertEquals(millis, repairLegacyNotificationTime(entry).diagnostic!!.observedAt)
        assertEquals(millis, repairLegacyNotificationTime(entry).events.single().observedAt)
        val native = entry.copy(diagnostic = error.copy(eventId = "native", endpointRole = "runtime_to_provider"), events = emptyList())
        assertEquals(native, repairLegacyNotificationTime(native))
    }

    @Test fun unknownToolFailureIsIdentifiedWithoutInventingItsCause() {
        val error = notificationEventFailure(event(result = "not a JSON response"), emptyList())!!
        assertEquals("TOOL.EXECUTION_FAILED", error.code); assertEquals("host_service", error.toolName)
        assertTrue(error.reasonCode.isBlank()); assertTrue(error.reasonDetail.isBlank())
        assertTrue(diagnosticExplanation(error, AppLanguage.English).contains("tool reported"))
    }

    @Test fun nestedErrorMessageSurvivesAndSecretsDoNotEnterTheReport() {
        val event = event(result = """{"error":{"code":"backend_failed","message":"Failed. api_key=EXCLUDED Bearer EXCLUDED https://user:EXCLUDED@fixture.test/path?token=EXCLUDED sk-EXCLUDED"}}""")
        val first = observe(event); val report = incidentDiagnosticReport(first.single(), "fixture", AppLanguage.English, millis + 3000)
        assertTrue(report.contains("backend_failed")); assertTrue(report.contains("Failed."))
        assertFalse(report.contains("EXCLUDED")); assertFalse(report.contains("tool_input")); assertFalse(report.contains("result_preview"))
        assertEquals(24, first.single().diagnostic!!.operationKey.length)
    }

    @Test fun bridgeSecondsAreConvertedOnceAndTypedMillisecondsAreNotChanged() {
        assertEquals(millis, bridgeEventTimeMillis(seconds)); assertEquals(millis, bridgeEventTimeMillis(millis))
        assertEquals(77L, bridgeEventTimeMillis(null, 77)); assertEquals(77L, bridgeEventTimeMillis(0, 77))
        val typed = ErrorObservation(observedAt = millis, code = "NET.READ_TIMEOUT")
        val e = event().let { it.copy(payload = JsonObject(it.payload + ("diagnostic" to Json.parseToJsonElement(Json.encodeToString(typed))))) }
        assertEquals(millis, notificationEventFailure(e, emptyList())!!.observedAt)
    }

    @Test fun compactionAndTaskFailuresRetainTheirStageAndExplicitReason() {
        val compact = notificationEventFailure(event(kind = "compaction.failed"), emptyList())!!
        assertEquals("CONTEXT.COMPACTION_FAILED", compact.code)
        val task = event(kind = "task.finish", status = "fail").let { it.copy(payload = JsonObject(it.payload + ("reason" to JsonPrimitive("fixture_reason")))) }
        val error = notificationEventFailure(task, emptyList())!!
        assertEquals("TASK.EXECUTION_FAILED", error.code); assertEquals("fixture_reason", error.reasonCode)
    }

    @Test fun aNewFailureAfterRecoveryRemainsANewActiveIncident() {
        val first = reconcileNotificationEvent(observe(), event(seq = 11, status = "done", input = """{"action":"files","service":"fixture"}"""))
        val error = notificationEventFailure(event(seq = 12), first)!!
        val result = mergeNotification(first, errorMessage(error), "OPERATION", millis + 5000, error)
        assertEquals(2, result.size); assertEquals(1, visibleNotifications(result).size); assertEquals(1, unreadNotificationCount(result))
    }
    @Test fun nestedTypedFailureKeepsItsOriginalCodeOutcomeAndOwner() {
        val typed = ErrorObservation(code = "OPERATION.OUTCOME_UNKNOWN", owner = "operation:fixture", outcome = "unknown",
            delivery = "accepted", component = "operation", operation = "send", reasonCode = "fixture_unconfirmed", observedAt = millis)
        val body = buildJsonObject { put("error", "operation_unconfirmed"); put("diagnostic", Json.parseToJsonElement(Json.encodeToString(typed))) }
        val error = notificationEventFailure(event(result = body.toString()), emptyList())!!
        assertEquals("OPERATION.OUTCOME_UNKNOWN", error.code); assertEquals("unknown", error.outcome)
        assertEquals("operation:fixture", error.owner); assertEquals("accepted", error.delivery)
        assertEquals("host_service", error.toolName); assertEquals(millis, error.observedAt)
    }

    @Test fun rejectedExportsForDifferentFilesCannotBeMistakenForTheSameOperation() {
        val first = observe(event(input = """{"action":"export","service":"fixture","file_id":"a","unexpected":true}"""))
        val next = event(seq = 11, status = "done", input = """{"action":"export","service":"fixture","file_id":"b"}""")
        assertEquals(first, reconcileNotificationEvent(first, next))
    }

    @Test fun excessivelyNestedInputsDoNotCreateAFalseRecoveryIdentity() {
        var nested: JsonElement = JsonPrimitive("a")
        repeat(20) { nested = buildJsonObject { put("child", nested) } }
        val e = event().let { it.copy(payload = JsonObject(it.payload + ("tool_input" to nested))) }
        assertEquals("", toolOperationKey(e, false))
    }

}
