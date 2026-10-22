package ru.igorek.remote.data

import java.time.Instant
import java.util.UUID
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*

internal const val DIAGNOSTIC_REPORT_MAX_BYTES = 32 * 1024
internal const val INCIDENT_REPORT_MAX_BYTES = 8 * 1024
internal const val REPORT_DETAIL_LIMIT = 10
internal const val REPORT_GROUP_LIMIT = 12
internal const val REPORT_EVENT_LIMIT = 4
internal const val RECOVERED_REPORT_WINDOW_MS = 24 * 60 * 60 * 1000L
private val supportJson = Json { ignoreUnknownKeys = true; encodeDefaults = true; prettyPrint = true }

internal data class DiagnosticSupportDraft(val reportId: String, val json: String) {
    val recipient: String get() = ErrorCatalog.supportEmail
    val fileName: String get() = "igorek-diagnostics-$reportId.json"
    val subject: String get() = "[Igorek Diagnostics] $reportId"
}

private fun reference(value: String?): String = value?.takeIf {
    it.matches(Regex("[A-Za-z0-9_.:-]{1,160}")) && !it.matches(Regex("^(sk-|gh[pousr]_|github_pat_|eyJ).*"))
}.orEmpty()

private fun primitiveText(value: JsonElement?): String? = (value as? JsonPrimitive)?.contentOrNull
private fun number(value: JsonElement?): Long = ((value as? JsonPrimitive)?.longOrNull ?: 0).coerceIn(0,999999999999999)
private fun date(value: JsonElement?): String = number(value).takeIf { it > 0 }?.let { Instant.ofEpochMilli(it).toString() }.orEmpty()
private fun text(ru: String, en: String, language: AppLanguage) = if (language == AppLanguage.Russian) ru else en

private fun diagnostic(value: JsonElement?): JsonObject? = runCatching {
    val original = value as? JsonObject ?: return null
    val parsed = supportJson.decodeFromString<ErrorObservation>(original.toString())
    val normalized = supportJson.parseToJsonElement(supportJson.encodeToString(sanitizeDiagnostic(parsed))) as JsonObject
    buildJsonObject {
        for ((key, item) in normalized) {
            if (key == "schema_version" || key !in original && key != "code" || item == JsonNull) continue
            val valueText = (item as? JsonPrimitive)?.takeIf { it.isString }?.content
            if (valueText != null && (valueText.isBlank() || valueText.matches(Regex("^(sk-|gh[pousr]_|github_pat_|eyJ).*")))) continue
            if (key == "model" && valueText != null) {
                val path = valueText.replace('\\', '/')
                val local = path.startsWith('/') || path.startsWith("~/") || path.startsWith("./") ||
                    path.startsWith("../") || (path.length > 2 && path[1] == ':' && path[2] == '/') || path.endsWith(".gguf", true)
                put(key, if (local) path.substringAfterLast('/') else valueText)
            } else if (key in setOf("observed_at_ms", "next_retry_at_ms")) {
                date(item).takeIf { it.isNotBlank() }?.let { put(key.removeSuffix("_ms") + "_utc", it) }
            } else put(key, item)
        }
        val definition = ErrorCatalog.codes.getValue(sanitizeDiagnostic(parsed).code)
        put("category", definition.category); put("severity", definition.severity); put("action", definition.action)
    }
}.getOrNull()

private fun normalizedIncident(item: JsonObject, language: AppLanguage): JsonObject = buildJsonObject {
    put("incident_id", reference(primitiveText(item["incident_id"] ?: item["id"])))
    val state = primitiveText(item["state"])?.takeIf { it in setOf("observed", "recovering", "needs_owner", "recovered") } ?: "observed"
    put("state", state)
    put("status", when (state) {
        "recovered" -> text("Восстановлено", "Recovered", language)
        "recovering" -> text("Восстанавливается", "Recovering", language)
        "needs_owner" -> text("Требует внимания", "Needs attention", language)
        else -> text("Сбой зарегистрирован", "Failure observed", language)
    })
    val error = diagnostic(item["error"] ?: item["diagnostic"])
    val code = primitiveText(error?.get("code")) ?: "ERROR.UNKNOWN"
    val definition = ErrorCatalog.codes.getValue(code)
    put("explanation", if (error != null) diagnosticExplanation(supportJson.decodeFromString<ErrorObservation>(error.toString()), language)
        else text(definition.ru, definition.en, language))
    put("observations", number(item["observations"] ?: item["count"]).coerceIn(1,1000000000))
    for (key in listOf("first_seen_ms", "last_seen_ms", "recovered_at_ms")) {
        val stamp = date(item[key])
        if (stamp.isNotBlank()) put(key.removeSuffix("_ms") + "_utc", stamp)
    }
    primitiveText(item["recovery_proof"])?.takeIf { it in setOf("stable_ready", "operation_completed", "operation_confirmed", "tool_retry_succeeded", "task_completed_after_rejected_call") }
        ?.let {
            put("recovery_proof", it)
            notificationRecoveryExplanation(it, language).takeIf(String::isNotBlank)?.let { message -> put("recovery_explanation", message) }
        }
    if (error != null) put("error", error)
    val allEvents = (item["events"] as? JsonArray).orEmpty().mapNotNull(::diagnostic).distinct()
    val earlier = allEvents.filter { event ->
        val id = primitiveText(event["event_id"])
        if (!id.isNullOrBlank()) id != primitiveText(error?.get("event_id")) else event != error
    }.takeLast(REPORT_EVENT_LIMIT)
    put("earlier_events", JsonArray(earlier.map { event -> buildJsonObject {
        for (key in (event.keys + error?.keys.orEmpty())) {
            val value = event[key] ?: JsonNull
            if (key in setOf("event_id", "observed_at_utc", "attempt") || value != error?.get(key)) put(key, value)
        }
    } }))
    put("events_omitted", (number(item["observations"] ?: item["count"]).coerceAtLeast(1) - earlier.size - if (error == null) 0 else 1).coerceAtLeast(0))
}

private fun compactReport(snapshot: String, version: String, language: AppLanguage, at: Long,
    single: Boolean): DiagnosticSupportDraft {
    val source = if (snapshot.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024)
        runCatching { supportJson.parseToJsonElement(snapshot) as? JsonObject }.getOrNull() else null
    val input = (source?.get("incidents") as? JsonArray).orEmpty()
    val items = input.takeLast(100).mapNotNull { it as? JsonObject }
    val normalized = items.map { normalizedIncident(it, language) }
    val active = normalized.filter { primitiveText(it["state"]) != "recovered" }
    val eligible = items.zip(normalized).filter { (raw, value) -> primitiveText(value["state"]) != "recovered" ||
        maxOf(number(raw["recovered_at_ms"]), number(raw["last_seen_ms"])) >= at - RECOVERED_REPORT_WINDOW_MS }
    val groupRows = eligible.map { it.second }.groupBy { item ->
        val error = item["error"] as? JsonObject
        listOf("code", "component", "endpoint_role", "http_status", "native_code").map { primitiveText(error?.get(it)).orEmpty() } +
            primitiveText(item["state"]).orEmpty()
    }.values.sortedWith(compareBy<List<JsonObject>> { group -> primitiveText(group.first()["state"]) == "recovered" }
        .thenByDescending { group -> group.maxOf { primitiveText(it["last_seen_utc"]).orEmpty() } })
    val groups = groupRows.take(REPORT_GROUP_LIMIT).map { group -> buildJsonObject {
        val first = group.first(); val error = first["error"] as? JsonObject
        for (key in listOf("code", "component", "endpoint_role", "http_status", "native_code")) error?.get(key)?.let { put(key, it) }
        put("state", first.getValue("state")); put("explanation", first.getValue("explanation"))
        put("incidents", group.size); put("observations", group.sumOf { number(it["observations"]) })
        group.mapNotNull { primitiveText(it["first_seen_utc"]) }.minOrNull()?.let { put("first_seen_utc", it) }
        group.mapNotNull { primitiveText(it["last_seen_utc"]) }.maxOrNull()?.let { put("last_seen_utc", it) }
    } }.toMutableList()
    val details = (if (single) normalized.take(1) else active.sortedWith(
        compareByDescending<JsonObject> { primitiveText(it["state"]) == "needs_owner" ||
            primitiveText((it["error"] as? JsonObject)?.get("outcome")) == "unknown" }
            .thenByDescending { primitiveText(it["last_seen_utc"]).orEmpty() }).take(REPORT_DETAIL_LIMIT)).toMutableList()
    val id = UUID.randomUUID().toString().replace("-", "")
    val maxBytes = if (single) INCIDENT_REPORT_MAX_BYTES else DIAGNOSTIC_REPORT_MAX_BYTES
    var omittedEventsForSize = 0
    fun encode(): String = supportJson.encodeToString(buildJsonObject {
        put("schema_version", 2); put("report_kind", if (single) "incident" else "overview"); put("report_id", id)
        put("captured_at_utc", Instant.ofEpochMilli(at.coerceAtLeast(0)).toString())
        put("app_version", version.takeIf { it.matches(Regex("[0-9][0-9A-Za-z.+-]{0,39}")) } ?: "unknown")
        put("surface", "android")
        put("summary", buildJsonObject {
            put("scope", if (single) text("Только выбранный инцидент", "Selected incident only", language)
                else text("Активные инциденты и сводка восстановленных за последние 24 часа", "Active incidents and grouped recoveries from the last 24 hours", language))
            put("retained_incidents", input.size); put("active_incidents", active.size)
            put("recovered_incidents", normalized.size - active.size)
            put("older_recoveries_excluded", normalized.size - eligible.size)
            put("detail_incidents", details.size)
        })
        put("limits", buildJsonObject {
            put("max_bytes", maxBytes); put("max_detail_incidents", if (single) 1 else REPORT_DETAIL_LIMIT)
            put("max_earlier_events_per_incident", REPORT_EVENT_LIMIT); put("max_groups", if (single) 0 else REPORT_GROUP_LIMIT)
            put("recovered_history_hours", 24); put("journal_max_incidents", NOTIFICATION_LIMIT)
        })
        put("event_encoding", text("В error — последнее событие. В earlier_events — только отличающиеся поля прежних событий.",
            "error is the latest event. earlier_events contain only fields differing from the latest event.", language))
        put("local", buildJsonObject {
            put("available", source?.get("incidents") is JsonArray)
            put("health", buildJsonObject {
                put("available", source?.containsKey("write_failures") == true)
                source?.get("write_failures")?.let { put("write_failures", number(it)) }
            })
            put("incidents", JsonArray(details)); put("omitted_incidents", (input.size - details.size).coerceAtLeast(0))
        })
        if (!single) put("groups", JsonArray(groups))
        put("omissions", buildJsonObject {
            put("input_incidents_unprocessed", input.size - normalized.size)
            put("groups_omitted", if (single) 0 else groupRows.size - groups.size)
            put("events_omitted_for_size", omittedEventsForSize)
        })
        put("truncated", input.size > details.size || omittedEventsForSize > 0 || groupRows.size > groups.size ||
            details.any { number(it["events_omitted"]) > 0 })
        put("server", buildJsonObject { put("available", false) })
    })
    var encoded = encode()
    while (encoded.toByteArray(Charsets.UTF_8).size > maxBytes) {
        val index = details.indexOfLast { (it["earlier_events"] as? JsonArray).orEmpty().isNotEmpty() }
        when {
            index >= 0 -> {
                val item = details[index]
                details[index] = JsonObject(item + ("earlier_events" to JsonArray((item["earlier_events"] as JsonArray).drop(1))) +
                    ("events_omitted" to JsonPrimitive(number(item["events_omitted"]) + 1)))
                omittedEventsForSize++
            }
            !single && details.isNotEmpty() -> details.removeAt(details.lastIndex)
            groups.isNotEmpty() -> groups.removeAt(groups.lastIndex)
            else -> error("diagnostic_report_limit")
        }
        encoded = encode()
    }
    return DiagnosticSupportDraft(id, encoded)
}

internal fun prepareDiagnosticSupport(snapshot: String, version: String, language: AppLanguage = AppLocale.current,
    at: Long = System.currentTimeMillis()): DiagnosticSupportDraft = compactReport(snapshot, version, language, at, false)

internal fun incidentDiagnosticReport(entry: NotificationEntry, version: String,
    language: AppLanguage = AppLocale.current, at: Long = System.currentTimeMillis()): String {
    val snapshot = buildJsonObject { put("incidents", JsonArray(listOf(buildJsonObject {
        put("incident_id", entry.id); put("state", entry.state); put("observations", entry.count)
        put("first_seen_ms", entry.firstSeen); put("last_seen_ms", entry.lastSeen)
        entry.recoveredAt?.let { put("recovered_at_ms", it) }; put("recovery_proof", entry.recoveryProof)
        entry.diagnostic?.let { put("error", supportJson.parseToJsonElement(supportJson.encodeToString(it))) }
        put("events", supportJson.parseToJsonElement(supportJson.encodeToString(entry.events.takeLast(8))))
    }))) }
    return compactReport(snapshot.toString(), version, language, at, true).json
}
