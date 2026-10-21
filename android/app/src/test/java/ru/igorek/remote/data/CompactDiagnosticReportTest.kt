package ru.igorek.remote.data

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class CompactDiagnosticReportTest {
    private val now = 1800000000000L
    private val codec = Json { encodeDefaults = true }
    private fun error(id: String = "latest") = ErrorObservation(eventId = id, observedAt = now,
        code = "HTTP.SERVICE_UNAVAILABLE", component = "model", owner = "model:fixture", phase = "http_body",
        taskId = "task-fixture", sessionId = "chat-fixture", httpRequestId = "req-fixture", httpStatus = 500)
    private fun entry(id: String = "chosen", state: String = "observed", at: Long = now,
        count: Int = 1, events: List<ErrorObservation> = emptyList()) = NotificationEntry("Private unstructured text",
        id = id, state = state, firstSeen = at - 10000, lastSeen = at, count = count,
        recoveredAt = if (state == "recovered") at else null, recoveryProof = if (state == "recovered") "stable_ready" else "",
        diagnostic = error(), events = events)
    private fun snapshot(entries: List<NotificationEntry>): String = buildJsonObject {
        put("write_failures", 2)
        put("incidents", JsonArray(entries.map { value -> buildJsonObject {
            put("incident_id", value.id); put("state", value.state); put("observations", value.count)
            put("first_seen_ms", value.firstSeen); put("last_seen_ms", value.lastSeen)
            value.recoveredAt?.let { put("recovered_at_ms", it) }
            value.diagnostic?.let { put("error", codec.parseToJsonElement(codec.encodeToString(it))) }
            put("events", codec.parseToJsonElement(codec.encodeToString(value.events)))
        } }))
    }.toString()
    private fun report(entries: List<NotificationEntry>) = Json.parseToJsonElement(
        prepareDiagnosticSupport(snapshot(entries), "0.1.117", AppLanguage.English, now).json).jsonObject
    private fun details(report: JsonObject) = report.getValue("local").jsonObject.getValue("incidents").jsonArray

    @Test fun selectedCopyContainsOneIncidentAndNoUnrelatedHistory() {
        val copied = incidentDiagnosticReport(entry(), "0.1.117", AppLanguage.English, now)
        val parsed = Json.parseToJsonElement(copied).jsonObject
        assertEquals("incident", parsed.getValue("report_kind").jsonPrimitive.content)
        assertEquals(1, details(parsed).size)
        assertEquals("chosen", details(parsed).single().jsonObject.getValue("incident_id").jsonPrimitive.content)
        assertFalse(copied.contains("Private unstructured text"))
        assertEquals("Selected incident only", parsed.getValue("summary").jsonObject.getValue("scope").jsonPrimitive.content)
    }

    @Test fun selectedIncidentIncludesReadableTimeStateAndExactCorrelationReferences() {
        val parsed = Json.parseToJsonElement(incidentDiagnosticReport(entry(), "0.1.117", AppLanguage.English, now)).jsonObject
        val item = details(parsed).single().jsonObject
        assertEquals("Failure observed", item.getValue("status").jsonPrimitive.content)
        assertTrue(item.getValue("last_seen_utc").jsonPrimitive.content.endsWith("Z"))
        val e = item.getValue("error").jsonObject
        assertEquals(500, e.getValue("http_status").jsonPrimitive.int)
        assertEquals("task-fixture", e.getValue("task_id").jsonPrimitive.content)
        assertEquals("req-fixture", e.getValue("http_request_id").jsonPrimitive.content)
        assertFalse(e.containsKey("provider_request_id"))
        assertFalse(e.containsKey("observed_at_ms"))
        assertTrue(e.containsKey("observed_at_utc"))
    }

    @Test fun recoveredSelectionKeepsItsProofEvenWhenOlderThanTheOverviewWindow() {
        val parsed = Json.parseToJsonElement(incidentDiagnosticReport(entry(state = "recovered", at = now - 2 * RECOVERED_REPORT_WINDOW_MS),
            "0.1.117", AppLanguage.Russian, now)).jsonObject
        val item = details(parsed).single().jsonObject
        assertEquals("Восстановлено", item.getValue("status").jsonPrimitive.content)
        assertEquals("stable_ready", item.getValue("recovery_proof").jsonPrimitive.content)
        assertTrue(item.containsKey("recovered_at_utc"))
    }

    @Test fun earlierEventsStoreOnlyDifferencesAndNeverDuplicateTheLatestEvent() {
        val previous = error("earlier").copy(observedAt = now - 1000, attempt = 2)
        val parsed = Json.parseToJsonElement(incidentDiagnosticReport(entry(count = 3,
            events = listOf(previous, previous, error())), "0.1.117", AppLanguage.English, now)).jsonObject
        val item = details(parsed).single().jsonObject
        val events = item.getValue("earlier_events").jsonArray
        assertEquals(1, events.size)
        val delta = events.single().jsonObject
        assertEquals("earlier", delta.getValue("event_id").jsonPrimitive.content)
        assertEquals(2, delta.getValue("attempt").jsonPrimitive.int)
        assertFalse(delta.containsKey("owner")); assertFalse(delta.containsKey("task_id")); assertFalse(delta.containsKey("code"))
        assertEquals(1, item.getValue("events_omitted").jsonPrimitive.int)
    }

    @Test fun earlierMissingFieldsAreExplicitRatherThanCopiedFromTheLatestError() {
        val previous = error("before-response").copy(httpStatus = null)
        val parsed = Json.parseToJsonElement(incidentDiagnosticReport(entry(count = 2, events = listOf(previous)),
            "0.1.117", AppLanguage.English, now)).jsonObject
        val delta = details(parsed).single().jsonObject.getValue("earlier_events").jsonArray.single().jsonObject
        assertEquals(JsonNull, delta["http_status"])
    }

    @Test fun selectedReportHasABoundedTimelineAndDisclosesOmittedObservations() {
        val events = (0 until 8).map { error("event-$it").copy(attempt = it + 1, observedAt = now - (8 - it)) }
        val item = details(Json.parseToJsonElement(incidentDiagnosticReport(entry(count = 50, events = events),
            "0.1.117", AppLanguage.English, now)).jsonObject).single().jsonObject
        assertEquals(REPORT_EVENT_LIMIT, item.getValue("earlier_events").jsonArray.size)
        assertEquals(45, item.getValue("events_omitted").jsonPrimitive.int)
    }

    @Test fun recoveredIncidentsAreGroupedWithoutRepeatingTheirFullErrors() {
        val parsed = report((0 until 40).map { entry("recovered-$it", "recovered", count = 3) })
        assertTrue(details(parsed).isEmpty())
        val group = parsed.getValue("groups").jsonArray.single().jsonObject
        assertEquals(40, group.getValue("incidents").jsonPrimitive.int)
        assertEquals(120, group.getValue("observations").jsonPrimitive.int)
        assertEquals("recovered", group.getValue("state").jsonPrimitive.content)
        assertFalse(parsed.toString().contains("recovered-39"))
    }

    @Test fun oldRecoveriesAreExcludedButOldUnresolvedFailuresRemain() {
        val old = now - 2 * RECOVERED_REPORT_WINDOW_MS
        val parsed = report(listOf(entry("old-recovery", "recovered", old), entry("old-active", at = old)))
        assertEquals("old-active", details(parsed).single().jsonObject.getValue("incident_id").jsonPrimitive.content)
        assertEquals(1, parsed.getValue("summary").jsonObject.getValue("older_recoveries_excluded").jsonPrimitive.int)
        assertEquals(1, parsed.getValue("groups").jsonArray.size)
    }

    @Test fun unresolvedOutcomeGetsDetailPriorityOverNewerRoutineFailures() {
        val unknown = entry("unknown-send", "needs_owner", now - RECOVERED_REPORT_WINDOW_MS).copy(
            diagnostic = error().copy(outcome = "unknown", operation = "send"))
        val parsed = report(listOf(unknown) + (0 until 30).map { entry("routine-$it", at = now + it) })
        assertEquals(REPORT_DETAIL_LIMIT, details(parsed).size)
        assertEquals("unknown-send", details(parsed).first().jsonObject.getValue("incident_id").jsonPrimitive.content)
        assertEquals(21, parsed.getValue("local").jsonObject.getValue("omitted_incidents").jsonPrimitive.int)
        assertEquals(2, parsed.getValue("local").jsonObject.getValue("health").jsonObject.getValue("write_failures").jsonPrimitive.int)
    }

    @Test fun unrelatedCodesAndHttpStatusesDoNotMergeIntoOneGroup() {
        val a = entry("a", "recovered")
        val b = entry("b", "recovered").copy(diagnostic = error().copy(httpStatus = 503))
        val c = entry("c", "recovered").copy(diagnostic = error().copy(code = "NET.CONNECTION_RESET", httpStatus = null))
        assertEquals(3, report(listOf(a, b, c)).getValue("groups").jsonArray.size)
    }

    @Test fun groupCountIsBoundedAndOmissionsAreCounted() {
        val parsed = report((0 until 30).map { entry("entry-$it", "recovered").copy(diagnostic = error().copy(nativeCode = "Type$it")) })
        assertEquals(REPORT_GROUP_LIMIT, parsed.getValue("groups").jsonArray.size)
        assertEquals(18, parsed.getValue("omissions").jsonObject.getValue("groups_omitted").jsonPrimitive.int)
    }

    @Test fun filePathsAndCredentialsCannotLeakThroughModelMetadata() {
        val raw = entry().copy(diagnostic = error().copy(model = "/srv/private/models/model.gguf", owner = "sk-SECRET", profile = "github_pat_SECRET"))
        val value = incidentDiagnosticReport(raw, "0.1.117", AppLanguage.English, now)
        assertFalse(value.contains("/srv/private")); assertFalse(value.contains("SECRET"))
        assertTrue(value.contains("model.gguf"))
        val cloud = incidentDiagnosticReport(entry().copy(diagnostic = error().copy(model = "org/model")), "0.1.117", at = now)
        assertTrue(cloud.contains("org/model"))
    }

    @Test fun maximumUtf8SizeAppliesToBothReportKindsAndKeepsTheSelectedIncident() {
        val huge = error().copy(component = "c".repeat(160), owner = "o".repeat(160), operation = "r".repeat(160),
            phase = "p".repeat(160), endpointRole = "e".repeat(160), httpRequestId = "q".repeat(160),
            clientOperationId = "x".repeat(160), providerRequestId = "y".repeat(160), taskId = "t".repeat(160),
            sessionId = "s".repeat(160), profile = "f".repeat(160), model = "m".repeat(160), nativeCode = "n".repeat(160))
        val entries = (0 until 50).map { i -> entry("huge-$i", count = 9,
            events = (0 until 8).map { huge.copy(eventId = "event-$it", owner = "changed-$it" + "o".repeat(145)) }).copy(diagnostic = huge) }
        val single = incidentDiagnosticReport(entries.first(), "0.1.117", AppLanguage.Russian, now)
        assertTrue(single.toByteArray(Charsets.UTF_8).size <= INCIDENT_REPORT_MAX_BYTES)
        assertEquals(1, details(Json.parseToJsonElement(single).jsonObject).size)
        val overall = prepareDiagnosticSupport(snapshot(entries), "0.1.117", AppLanguage.Russian, now).json
        assertTrue(overall.toByteArray(Charsets.UTF_8).size <= DIAGNOSTIC_REPORT_MAX_BYTES)
        assertTrue(Json.parseToJsonElement(overall).jsonObject.getValue("truncated").jsonPrimitive.boolean)
    }

    @Test fun legacyNotificationStillProducesASelectedReportWithoutInventingReferences() {
        val parsed = Json.parseToJsonElement(incidentDiagnosticReport(entry().copy(diagnostic = null), "0.1.117", at = now)).jsonObject
        val item = details(parsed).single().jsonObject
        assertFalse(item.containsKey("error"))
        assertEquals("chosen", item.getValue("incident_id").jsonPrimitive.content)
        assertFalse(parsed.getValue("local").jsonObject.getValue("health").jsonObject.getValue("available").jsonPrimitive.boolean)
    }
}
