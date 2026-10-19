package ru.igorek.remote.data

import java.util.UUID
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.booleanOrNull

internal const val DIAGNOSTIC_REPORT_MAX_BYTES = 512 * 1024
private val supportJson = Json { ignoreUnknownKeys = true; encodeDefaults = true; prettyPrint = true }

internal data class DiagnosticSupportDraft(val reportId: String, val json: String) {
    val recipient: String get() = ErrorCatalog.supportEmail
    val fileName: String get() = "igorek-diagnostics-$reportId.json"
    val subject: String get() = "[Igorek Diagnostics] $reportId"
}

private fun reference(value: String?): String = value?.takeIf {
    it.matches(Regex("[A-Za-z0-9_.:-]{1,160}")) && !it.matches(Regex("^(sk-|gh[pousr]_|github_pat_|eyJ).*"))
}.orEmpty()

private fun primitiveText(value: kotlinx.serialization.json.JsonElement?): String? = (value as? JsonPrimitive)?.contentOrNull
private fun number(value: kotlinx.serialization.json.JsonElement?): Long = ((value as? JsonPrimitive)?.longOrNull ?: 0).coerceIn(0,999999999999999)
private fun diagnostic(value: kotlinx.serialization.json.JsonElement?): JsonObject? = runCatching {
    val original = value as? JsonObject ?: return null
    val parsed = supportJson.decodeFromString<ErrorObservation>(original.toString())
    val normalized = supportJson.parseToJsonElement(supportJson.encodeToString(sanitizeDiagnostic(parsed))) as JsonObject
    buildJsonObject {
        for ((key, item) in normalized) {
            if(key !in original && key !in setOf("schema_version","code"))continue
            val text = (item as? JsonPrimitive)?.takeIf { it.isString }?.content
            if (text == null || !text.matches(Regex("^(sk-|gh[pousr]_|github_pat_|eyJ).*"))) put(key,item)
        }
        val definition=ErrorCatalog.codes.getValue(sanitizeDiagnostic(parsed).code)
        put("category",definition.category);put("severity",definition.severity);put("action",definition.action)
    }
}.getOrNull()

internal fun prepareDiagnosticSupport(snapshot: String, version: String): DiagnosticSupportDraft {
    val source = runCatching { supportJson.parseToJsonElement(snapshot) as? JsonObject }.getOrNull() ?: JsonObject(emptyMap())
    val values = (source["incidents"] as? JsonArray).orEmpty()
    val incidents = values.takeLast(100).mapNotNull { value ->
        val item = value as? JsonObject ?: return@mapNotNull null
        val error = diagnostic(item["error"] ?: item["diagnostic"]) ?: return@mapNotNull null
        buildJsonObject {
            put("incident_id",reference(primitiveText(item["incident_id"] ?: item["id"])))
            put("state",primitiveText(item["state"])?.takeIf { it in setOf("observed","recovering","needs_owner","recovered") } ?: "observed")
            put("read",(item["read"] as? JsonPrimitive)?.booleanOrNull == true)
            put("observations",number(item["observations"] ?: item["count"]).coerceIn(1,1000000000))
            for (key in listOf("first_seen_ms","last_seen_ms","recovered_at_ms")) put(key,number(item[key]))
            put("recovery_proof",primitiveText(item["recovery_proof"])?.takeIf { it in setOf("stable_ready","operation_completed","operation_confirmed") }.orEmpty())
            put("error",error)
            put("events",JsonArray((item["events"] as? JsonArray).orEmpty().takeLast(8).mapNotNull(::diagnostic)))
        }
    }.toMutableList()
    val id = UUID.randomUUID().toString().replace("-", "")
    val captured = System.currentTimeMillis()
    var omitted = (values.size-incidents.size).coerceAtLeast(0)
    fun encode(): String = supportJson.encodeToString(buildJsonObject {
        put("schema_version",1);put("report_id",id);put("captured_at_ms",captured)
        put("app_version",version.takeIf { it.matches(Regex("[0-9][0-9A-Za-z.+-]{0,39}")) } ?: "unknown")
        put("surface","android");put("truncated",omitted>0)
        put("local",buildJsonObject {
            put("available",source["incidents"] is JsonArray)
            put("health",buildJsonObject { put("write_failures",number(source["write_failures"])) })
            put("incidents",JsonArray(incidents));put("omitted_incidents",omitted)
        })
        put("server",buildJsonObject { put("available",false) })
    })
    var text=encode()
    while(text.toByteArray(Charsets.UTF_8).size>DIAGNOSTIC_REPORT_MAX_BYTES && incidents.isNotEmpty()) {
        val remove=maxOf(1,incidents.size/4)
        repeat(remove) { incidents.removeAt(0) };omitted+=remove;text=encode()
    }
    check(text.toByteArray(Charsets.UTF_8).size<=DIAGNOSTIC_REPORT_MAX_BYTES)
    return DiagnosticSupportDraft(id,text)
}
