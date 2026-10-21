package ru.igorek.remote.data

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class DiagnosticSupportTest {
    @Test fun sharedFixtureKeepsReferencesAndDropsPrivateFields() {
        val source=javaClass.classLoader!!.getResourceAsStream("support-report.json")!!.reader(Charsets.UTF_8).readText()
        val input=Json.parseToJsonElement(source).jsonObject.getValue("local")
        val draft=prepareDiagnosticSupport(input.toString(),"0.2.13")
        val report=Json.parseToJsonElement(draft.json).jsonObject
        val error=report.getValue("local").jsonObject.getValue("incidents").jsonArray.first().jsonObject.getValue("error").jsonObject
        assertEquals("req_fixture",error.getValue("http_request_id").jsonPrimitive.content)
        assertEquals("org/model",error.getValue("model").jsonPrimitive.content)
        assertEquals("network",error.getValue("category").jsonPrimitive.content)
        assertFalse(draft.json.contains("EXCLUDED"))
        assertEquals("igorek-dot@tutamail.com",draft.recipient)
        assertTrue(draft.fileName.matches(Regex("igorek-diagnostics-[a-f0-9]{32}\\.json")))
        assertTrue(draft.subject.contains(draft.reportId))
        assertFalse(report.getValue("server").jsonObject.getValue("available").jsonPrimitive.boolean)
    }
    @Test fun emptyOrMalformedHistoryDoesNotInventServerAvailability() {
        for (source in listOf("{}","[]","bad")) {
            val report=Json.parseToJsonElement(prepareDiagnosticSupport(source,"bad version").json).jsonObject
            assertEquals("unknown",report.getValue("app_version").jsonPrimitive.content)
            assertFalse(report.getValue("local").jsonObject.getValue("available").jsonPrimitive.boolean)
        }
    }
    @Test fun excessiveHistoryIsBoundedAndDisclosesOmissions() {
        val input=buildJsonObject { put("incidents",JsonArray((0..150).map { id -> buildJsonObject {
            put("incident_id","fixture-$id");put("state","observed");put("observations",1)
            put("error",buildJsonObject {put("code","NET.CONNECTION_RESET");put("owner","fixture")})
        } })) }
        val draft=prepareDiagnosticSupport(input.toString(),"0.2.13")
        assertTrue(draft.json.toByteArray(Charsets.UTF_8).size<=DIAGNOSTIC_REPORT_MAX_BYTES)
        val report=Json.parseToJsonElement(draft.json).jsonObject
        assertTrue(report.getValue("truncated").jsonPrimitive.boolean)
        assertEquals(REPORT_DETAIL_LIMIT,report.getValue("local").jsonObject.getValue("incidents").jsonArray.size)
    }
    @Test fun missingSourceReferencesAreNotManufactured() {
        val snapshot="""{"incidents":[{"error":{"code":"NET.CONNECTION_RESET"}}]}"""
        val report=Json.parseToJsonElement(prepareDiagnosticSupport(snapshot,"0.2.13").json).jsonObject
        val item=report.getValue("local").jsonObject.getValue("incidents").jsonArray.first().jsonObject
        assertEquals("",item.getValue("incident_id").jsonPrimitive.content)
        val error=item.getValue("error").jsonObject
        assertFalse(error.containsKey("event_id"));assertFalse(error.containsKey("observed_at_ms"))
    }
    @Test fun maximumSizeAppliesToLargeEventMetadataAsUtf8Bytes() {
        val event=buildJsonObject {
            put("code","NET.CONNECTION_RESET");put("event_id","fixture-event")
            for(key in listOf("component","operation","owner","phase","endpoint_role","http_request_id","client_operation_id","provider_request_id","task_id","session_id","profile","model","native_code"))put(key,"a".repeat(160))
        }
        val snapshot=buildJsonObject { put("incidents",JsonArray((0 until 100).map { id -> buildJsonObject {
            put("incident_id","fixture-$id");put("error",event);put("events",JsonArray(List(8){event}))
        } })) }
        val draft=prepareDiagnosticSupport(snapshot.toString(),"0.2.13")
        assertTrue(draft.json.toByteArray(Charsets.UTF_8).size<=DIAGNOSTIC_REPORT_MAX_BYTES)
        assertTrue(Json.parseToJsonElement(draft.json).jsonObject.getValue("truncated").jsonPrimitive.boolean)
    }

}
