package ru.igorek.remote.data

import kotlinx.serialization.json.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.decodeFromString
import org.junit.Assert.*
import org.junit.Test

class BrowserToolDiagnosticsTest {
    private fun event(input: String, reason: String = "invalid_request", tool: String = "desktop_browser",
        performed: Boolean = false, diagnostic: JsonObject? = null) = BridgeEvent(
        kind = "tool.result", seq = 10, ts = 1792729000L, taskId = "browser-task", sessionId = "browser-session",
        payload = buildJsonObject {
            put("status", "error"); put("tool_name", tool); put("tool_use_id", "browser-call")
            put("tool_input", Json.parseToJsonElement(input))
            put("context_output", buildJsonObject {
                put("error", reason); if (performed) put("performed", true)
                diagnostic?.let { put("diagnostic", it) }
            }.toString())
        })

    private fun observe(event: BridgeEvent): List<NotificationEntry> {
        val error = notificationEventFailure(event, emptyList())!!
        return mergeNotification(emptyList(), errorMessage(error), "OPERATION", 1792729000000L, error)
    }

    private fun finish(task: String = "browser-task", status: String = "ok") = BridgeEvent(
        kind = "task.finish", seq = 20, ts = 1792729010L, taskId = task, sessionId = "browser-session",
        payload = buildJsonObject { put("status", status) })

    @Test fun legacyBrowserParameterRejectionsAreAgentCorrectableAndCloseAfterTaskSuccess() {
        for (input in listOf(
            """{"action":"read","limit":1500}""",
            """{"action":"read","offset":9000,"limit":6000}""",
            """{"action":"collect","limit":20}""",
            """{"action":"result","limit":6000}""",
            """{"action":"scroll","direction":"down"}""",
            """{"action":"press","key":"PageUp"}""",
        )) {
            val first = observe(event(input)); val error = first.single().diagnostic!!
            assertEquals("TOOL.INVALID_ARGUMENTS", error.code)
            assertEquals("rejected", error.delivery); assertEquals("observed", first.single().state)
            assertEquals("invalid_request", error.reasonCode)
            val recovered = reconcileNotificationEvent(first, finish())
            assertEquals("task_completed_after_rejected_call", recovered.single().recoveryProof)
            assertTrue(visibleNotifications(recovered).isEmpty())
        }
    }

    @Test fun staleRefsAndUnsupportedPostUrlsAreNotReportedAsOwnerIntervention() {
        for ((input, reason) in listOf(
            """{"action":"click","target":"r-fixture"}""" to "stale_target",
            """{"action":"read_post","url":"https://x.com/fixture/status/123/history"}""" to "invalid_url",
        )) {
            val first = observe(event(input, reason))
            assertEquals("observed", first.single().state)
            assertEquals("recovered", reconcileNotificationEvent(first, finish()).single().state)
        }
    }

    @Test fun actualPostExtractionFailuresSurviveSuccessfulTaskCompletion() {
        val first = observe(event("""{"action":"read_post","url":"https://x.com/fixture/status/123"}""", "post_not_found"))
        assertEquals("TOOL.EXECUTION_FAILED", first.single().diagnostic!!.code)
        assertEquals("needs_owner", first.single().state)
        assertEquals(first, reconcileNotificationEvent(first, finish()))
    }

    @Test fun anUnknownToolOrPerformedActionIsNotAssumedToHaveBeenRejected() {
        for (call in listOf(
            event("""{"action":"read"}""", tool = "other_tool"),
            event("""{"action":"click"}""", "stale_target", performed = true),
        )) {
            val first = observe(call)
            assertEquals("TOOL.EXECUTION_FAILED", first.single().diagnostic!!.code)
            assertEquals(first, reconcileNotificationEvent(first, finish()))
        }
    }

    @Test fun typedRejectionsRetainTheirCauseAndUseTheReceivingBridgeScope() {
        val diagnostic = buildJsonObject {
            put("code", "TOOL.INVALID_ARGUMENTS"); put("component", "tool"); put("operation", "execute")
            put("endpoint_role", "runtime_to_tool"); put("delivery", "rejected"); put("outcome", "failed")
            put("reason_code", "invalid_request"); put("reason_detail", "read accepts action only.")
        }
        val first = observe(event("""{"action":"read","limit":6000}""", diagnostic = diagnostic))
        assertEquals("bridge_to_mobile", first.single().diagnostic!!.endpointRole)
        assertTrue(first.single().text.contains("read accepts action only"))
        assertEquals("recovered", reconcileNotificationEvent(first, finish()).single().state)
    }

    @Test fun replayRepairsExistingGenericBrowserIncidentsWithoutAddingOrDeletingCards() {
        val call = event("""{"action":"result","limit":6000}""")
        val first = observe(call).map { entry -> entry.copy(state = "needs_owner", read = true,
            diagnostic = entry.diagnostic!!.copy(code = "TOOL.EXECUTION_FAILED", delivery = "unknown")) }
        val repaired = reconcileNotificationEvent(first, call)
        val recovered = reconcileNotificationEvent(repaired, finish())
        assertEquals(1, recovered.size); assertEquals(first.single().id, recovered.single().id)
        assertTrue(recovered.single().read); assertEquals(1, recovered.single().count)
        assertEquals("recovered", recovered.single().state)
        assertEquals(recovered, reconcileNotificationEvent(recovered, call))
    }

    @Test fun unrelatedOrFailedTasksDoNotResolveARejectedCall() {
        val first = observe(event("""{"action":"read","limit":6000}"""))
        assertEquals(first, reconcileNotificationEvent(first, finish(task = "other-task")))
        assertEquals(first, reconcileNotificationEvent(first, finish(status = "fail")))
    }

    @Test fun explicitUncertainOutcomeCannotBeClosedAsARejectedCall() {
        val diagnostic = buildJsonObject {
            put("code", "TOOL.EXECUTION_FAILED"); put("delivery", "unknown"); put("outcome", "unknown")
        }
        val first = observe(event("""{"action":"read"}""", diagnostic = diagnostic))
        assertEquals("needs_owner", first.single().state)
        assertEquals(first, reconcileNotificationEvent(first, finish()))
    }

    @Test fun emptyTraceDiagnosticDoesNotCreateAnInventedInternalError() {
        val failed = finish(status = "fail").let { it.copy(payload = buildJsonObject {
            put("status", "fail"); put("reason", "reasoning_only_response_repeated"); put("diagnostic", buildJsonObject {})
        }) }
        val failure = notificationEventFailure(failed, emptyList())!!
        assertEquals("TASK.EXECUTION_FAILED", failure.code)
        assertEquals("reasoning_only_response_repeated", failure.reasonCode)
        assertEquals("bridge_to_mobile", failure.endpointRole)
        val known = observe(event("""{"action":"read"}""", "post_not_found"))
        assertNull(notificationEventFailure(failed, known))
    }

    @Test fun emptyToolDiagnosticDoesNotOverrideTheConcreteRejection() {
        val first = observe(event("""{"action":"read","limit":1500}""", diagnostic = buildJsonObject {}))
        assertEquals("TOOL.INVALID_ARGUMENTS", first.single().diagnostic!!.code)
        assertEquals("invalid_request", first.single().diagnostic!!.reasonCode)
    }

    @Test fun untypedToolHintsReachTheCardAndExportedExplanation() {
        val call = event("""{"action":"read","limit":2000}""").let { original ->
            original.copy(payload = JsonObject(original.payload + ("context_output" to JsonPrimitive(
                """{"error":"invalid_request","hint":"Unsupported fields for read: limit. Allowed fields: action."}"""))))
        }
        val first = observe(call)
        val error = first.single().diagnostic!!
        assertTrue(error.reasonDetail.contains("Unsupported fields for read: limit"))
        assertTrue(diagnosticExplanation(error, AppLanguage.Russian).contains("Allowed fields: action"))
        assertTrue(diagnosticExplanation(error, AppLanguage.English).contains("Allowed fields: action"))
    }

    @Test fun detailedUrlAndTimeoutCausesAreNeverReplacedByGenericLabels() {
        for (reason in listOf("invalid_url", "desktop_browser_timeout")) {
            val detail = "Exact fixture cause at desktop reply boundary."
            val diagnostic = buildJsonObject {
                put("code", "TOOL.EXECUTION_FAILED"); put("component", "tool"); put("operation", "execute")
                put("reason_code", reason); put("reason_detail", detail)
            }
            val error = observe(event("""{"action":"elements"}""", reason, diagnostic = diagnostic)).single().diagnostic!!
            assertTrue(diagnosticExplanation(error, AppLanguage.Russian).contains(detail))
            assertTrue(diagnosticExplanation(error, AppLanguage.English).contains(detail))
        }
    }

    @Test fun fieldCauseSurvivesPersistenceAndBothExportFormats() {
        val cause = "Unsupported fields for result: limit, offset. Allowed fields: action."
        val call = event("""{"action":"result","offset":0,"limit":6000}""").let { original ->
            original.copy(payload = JsonObject(original.payload + ("context_output" to JsonPrimitive(
                buildJsonObject { put("error", "invalid_request"); put("hint", cause) }.toString()))))
        }
        val codec = Json { encodeDefaults = true; ignoreUnknownKeys = true }
        val restored = codec.decodeFromString<List<NotificationEntry>>(codec.encodeToString(observe(call)))
        val snapshot = buildJsonObject { put("incidents", codec.encodeToJsonElement(restored)) }.toString()
        for (language in listOf(AppLanguage.Russian, AppLanguage.English)) {
            val overview = prepareDiagnosticSupport(snapshot, "fixture", language).json
            val single = incidentDiagnosticReport(restored.single(), "fixture", language)
            for (report in listOf(overview, single)) {
                val item = Json.parseToJsonElement(report).jsonObject.getValue("local").jsonObject
                    .getValue("incidents").jsonArray.single().jsonObject
                assertEquals(cause, item.getValue("error").jsonObject.getValue("reason_detail").jsonPrimitive.content)
                assertTrue(item.getValue("explanation").jsonPrimitive.content.contains(cause))
                assertFalse(report.contains("tool_input"))
            }
        }
    }

    @Test fun uncertainTimeoutRemainsActiveAndExplainedAfterReportExport() {
        val cause = "Desktop did not reply to elements within 45000 ms. Completion is unknown."
        val typed = buildJsonObject {
            put("code", "TOOL.EXECUTION_FAILED"); put("delivery", "unknown"); put("outcome", "unknown")
            put("phase", "desktop_reply"); put("reason_code", "desktop_browser_timeout"); put("reason_detail", cause)
        }
        val first = observe(event("""{"action":"elements"}""", "desktop_browser_timeout", diagnostic = typed))
        val after = reconcileNotificationEvent(first, finish())
        assertEquals("needs_owner", after.single().state)
        val report = Json.parseToJsonElement(incidentDiagnosticReport(after.single(), "fixture", AppLanguage.Russian)).jsonObject
        val item = report.getValue("local").jsonObject.getValue("incidents").jsonArray.single().jsonObject
        val error = item.getValue("error").jsonObject
        assertEquals("unknown", error.getValue("outcome").jsonPrimitive.content)
        assertEquals("desktop_reply", error.getValue("phase").jsonPrimitive.content)
        assertTrue(item.getValue("explanation").jsonPrimitive.content.contains("45000 ms"))
    }

    @Test fun detailedHintIsSanitizedBeforePersistenceAndExport() {
        val call = event("""{"action":"read"}""").let { original ->
            original.copy(payload = JsonObject(original.payload + ("context_output" to JsonPrimitive(
                """{"error":"invalid_request","hint":"Unsupported fields: limit. api_key=EXCLUDED Bearer EXCLUDED"}"""))))
        }
        val first = observe(call)
        assertTrue(first.single().diagnostic!!.reasonDetail.contains("limit"))
        assertFalse(first.single().text.contains("EXCLUDED"))
        val report = incidentDiagnosticReport(first.single(), "fixture", AppLanguage.English)
        assertTrue(report.contains("Unsupported fields"))
        assertFalse(report.contains("EXCLUDED"))
    }
}
