package ru.igorek.remote.data

import kotlinx.serialization.json.*

internal fun bridgeEventTimeMillis(value: Long?, now: Long = System.currentTimeMillis()): Long = when {
    value == null || value <= 0 -> now
    value < 100_000_000_000L -> value * 1000
    else -> value.coerceAtMost(999_999_999_999_999L)
}

internal fun toolDiagnosticId(value: String): String = value.takeIf {
    it.matches(Regex("[A-Za-z0-9_.:-]{1,160}")) &&
        !it.matches(Regex("^(sk[-_]|syt_|oc_sk_|gh[pousr]_|github_pat_|eyJ).*"))
}.orEmpty()

private fun JsonObject.string(key: String): String = (get(key) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun canonicalInput(value: JsonElement, depth: Int = 0): String {
    require(depth <= 16) { "input_too_deep" }
    return when (value) {
        is JsonObject -> value.keys.sorted().joinToString(",", "{", "}") { JsonPrimitive(it).toString() + ":" + canonicalInput(value.getValue(it), depth + 1) }
        is JsonArray -> value.joinToString(",", "[", "]") { canonicalInput(it, depth + 1) }
        else -> value.toString()
    }
}

private fun toolInput(event: BridgeEvent): JsonObject? = when (val input = event.payload["tool_input"]) {
    is JsonObject -> input
    is JsonPrimitive -> input.contentOrNull?.takeIf { it.length <= 65536 }
        ?.let { runCatching { Json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
    else -> null
}

internal fun toolOperationKey(event: BridgeEvent, validation: Boolean): String {
    val name = toolDiagnosticId(event.payload.string("tool_name"))
    val input = toolInput(event) ?: return ""
    if (name.isBlank()) return ""
    val selected = if (validation && name == "host_service") {
        val action = input.string("action")
        if (action.isBlank() || (action != "list" && input.string("service").isBlank())) return ""
        val keys = when (action) {
            "export" -> setOf("action", "service", "file_id")
            "invoke" -> setOf("action", "service", "method", "arguments")
            else -> setOf("action", "service")
        }
        JsonObject(input.filterKeys { it in keys })
    } else input
    val encoded = runCatching { canonicalInput(selected) }.getOrNull() ?: return ""
    if (encoded.length > 65536) return ""
    return connectionScope(name + "|" + encoded)
}

private val invalidToolReasons = setOf("invalid_service_fields", "invalid_arguments", "invalid_tool_input", "invalid_params", "schema_validation_error")

internal fun toolFailureObservation(event: BridgeEvent, base: ErrorObservation): ErrorObservation {
    val input = toolInput(event)
    val result = listOf("result_preview", "context_output").firstNotNullOfOrNull { key ->
        event.payload.string(key).takeIf { it.length <= 65536 && it.isNotBlank() }
            ?.let { runCatching { Json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
    }
    val error = result?.get("error")
    val objectError = error as? JsonObject
    val errorText = (error as? JsonPrimitive)?.contentOrNull.orEmpty()
    val reason = toolDiagnosticId(objectError?.string("code").orEmpty().ifBlank {
        result?.string("error_code").orEmpty().ifBlank { errorText.takeIf { it.matches(Regex("[A-Za-z][A-Za-z0-9_.:-]{1,100}")) }.orEmpty() }
    })
    val detail = objectError?.string("message").orEmpty().ifBlank {
        if (errorText != reason) errorText else ""
    }
    val typed = result?.get("diagnostic")?.let { value -> runCatching {
        Json { ignoreUnknownKeys = true }.decodeFromString<ErrorObservation>(value.toString())
    }.getOrNull() }?.takeIf { it.code in ErrorCatalog.codes }
    val validation = typed == null && reason in invalidToolReasons
    return sanitizeDiagnostic((typed?.copy(eventId = base.eventId, taskId = base.taskId, sessionId = base.sessionId,
        owner = typed.owner.ifBlank { base.owner }, observedAt = typed.observedAt) ?: base).copy(
        code = typed?.code ?: if (validation) "TOOL.INVALID_ARGUMENTS" else "TOOL.EXECUTION_FAILED",
        component = typed?.component ?: "tool", operation = typed?.operation ?: "execute", phase = typed?.phase ?: "tool_result",
        toolName = toolDiagnosticId(event.payload.string("tool_name")),
        toolUseId = toolDiagnosticId(event.payload.string("tool_use_id").ifBlank { event.payload.string("call_id") }),
        toolAction = toolDiagnosticId(input?.string("action").orEmpty()),
        service = toolDiagnosticId(input?.string("service").orEmpty()),
        reasonCode = typed?.reasonCode?.ifBlank { reason } ?: reason, reasonDetail = typed?.reasonDetail?.ifBlank { detail } ?: detail,
        operationKey = toolOperationKey(event, validation),
        sourceSequence = event.seq,
        delivery = typed?.delivery ?: if (validation) "rejected" else base.delivery,
    ))
}

internal fun diagnosticExplanation(error: ErrorObservation, language: AppLanguage): String {
    fun text(ru: String, en: String) = if (language == AppLanguage.Russian) ru else en
    val definition = ErrorCatalog.codes[error.code] ?: ErrorCatalog.codes.getValue("ERROR.UNKNOWN")
    val context = listOf(error.toolName, error.service, error.toolAction).filter { it.isNotBlank() }.joinToString(" → ")
    val title = if (language == AppLanguage.Russian) definition.ru else definition.en
    val reason = when (error.reasonCode) {
        "invalid_service_fields" -> text("Передан недопустимый набор полей вызова.", "The call contains an invalid set of fields.")
        "service_method_not_allowed" -> text("Запрошенный метод сервиса недоступен.", "The requested service method is not allowed.")
        "host_service_access_denied" -> text("Профилю не разрешён доступ к сервису.", "The profile is not allowed to access this service.")
        "invalid_url" -> text("Инструмент отклонил адрес страницы.", "The tool rejected the page address.")
        else -> error.reasonDetail.ifBlank { error.reasonCode }
    }
    val detail = reason.ifBlank {
        if (error.code == "TOOL.EXECUTION_FAILED") text("Инструмент не передал подробную причину; она доступна в исходном результате действия, если была записана.",
            "The tool did not provide a detailed cause; consult the original action result if it was recorded.") else ""
    }
    return listOf(context.takeIf { it.isNotBlank() }, title, detail.takeIf { it.isNotBlank() }).filterNotNull().joinToString("\n")
}

internal fun notificationRecoveryExplanation(proof: String, language: AppLanguage): String {
    val ru = language == AppLanguage.Russian
    return when (proof) {
        "tool_retry_succeeded" -> if (ru) "Исправлено: повторный вызов этой операции выполнен успешно." else "Recovered: a later call of this operation succeeded."
        "task_completed_after_rejected_call" -> if (ru) "Задача завершилась успешно после отклонённого вызова. Этот вызов не выполнялся." else "The task succeeded after a rejected call. That call was not executed."
        else -> ""
    }
}

internal fun repairLegacyNotificationTime(entry: NotificationEntry): NotificationEntry {
    fun repair(error: ErrorObservation): ErrorObservation = if (error.eventId.startsWith("event:") &&
        error.endpointRole == "bridge_to_mobile" && error.observedAt in 946684800L..4102444800L)
        error.copy(observedAt = error.observedAt * 1000) else error
    return entry.copy(diagnostic = entry.diagnostic?.let(::repair), events = entry.events.map(::repair))
}

internal fun reconcileNotificationEvent(entries: List<NotificationEntry>, event: BridgeEvent): List<NotificationEntry> {
    if (event.kind !in setOf("tool.result", "task.finish") || entries.isEmpty()) return entries
    val task = event.taskId.orEmpty().ifBlank { event.payload.string("task_id") }
    val session = event.sessionId.orEmpty()
    if (task.isBlank() || session.isBlank()) return entries
    val failure = if (event.kind == "tool.result") notificationEventFailure(event, emptyList()) else null
    val time = bridgeEventTimeMillis(event.ts)
    val status = event.payload.string("status")
    return entries.map { original ->
        val previous = original.diagnostic ?: return@map original
        if (previous.taskId != task || previous.sessionId != session || previous.endpointRole != "bridge_to_mobile") return@map original
        var entry = repairLegacyNotificationTime(original)
        if (failure != null && (previous.eventId == failure.eventId || previous.code == "ERROR.UNKNOWN" && previous.owner == failure.owner)) {
            entry = entry.copy(text = errorMessage(failure), diagnostic = failure,
                events = entry.events.map { if (it.eventId == previous.eventId) failure else it })
        }
        val error = entry.diagnostic ?: return@map entry
        if (entry.state == "recovered" || error.outcome == "unknown") return@map entry
        val later = if (error.sourceSequence != null && event.seq != null) event.seq > error.sourceSequence
            else time > error.observedAt
        if (!later) return@map entry
        val proof = when {
            event.kind == "tool.result" && status == "done" && error.component == "tool" && error.operationKey.isNotBlank() &&
                error.operationKey == toolOperationKey(event, error.code == "TOOL.INVALID_ARGUMENTS") -> "tool_retry_succeeded"
            event.kind == "task.finish" && status == "ok" && error.code == "TOOL.INVALID_ARGUMENTS" && error.delivery == "rejected" -> "task_completed_after_rejected_call"
            else -> return@map entry
        }
        entry.copy(state = "recovered", recoveredAt = time, recoveryProof = proof)
    }
}
