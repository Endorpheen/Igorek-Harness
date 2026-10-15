package ru.igorek.remote.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

private fun JsonObject.str(key: String): String =
    (this[key] as? JsonPrimitive)?.contentOrNull ?: ""

private fun JsonObject.long(key: String): Long =
    (this[key] as? JsonPrimitive)?.longOrNull ?: 0L

private fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject

private fun JsonObject.arr(key: String): JsonArray? = this[key] as? JsonArray

private fun JsonObject.strList(key: String): List<String> =
    arr(key)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }?.filter { it.isNotBlank() }.orEmpty()

private fun JsonElement.render(): String = when (this) {
    is JsonPrimitive -> contentOrNull ?: toString()
    else -> toString()
}

/** Stable user-message ID: one task may receive multiple session messages. */
internal fun userMessageItemId(taskId: String, messageId: String): String =
    if (messageId.isNotEmpty()) "user-$taskId-$messageId" else "user-$taskId"

private fun assistantMessageItemId(messageId: String): String = "assistant-message-$messageId"

private fun JsonObject.outputFiles(): List<AttachmentDto> = arr("files").orEmpty().mapNotNull { value ->
    val file = value as? JsonObject ?: return@mapNotNull null
    val id = file.str("id")
    val name = file.str("name")
    val size = (file["size"] as? JsonPrimitive)?.longOrNull ?: return@mapNotNull null
    if (id.isBlank() || name.isBlank() || size < 0) return@mapNotNull null
    AttachmentDto(id, name, size, file.str("mime_type").ifBlank { "application/octet-stream" })
}

private fun formatToolInput(name: String, input: JsonObject): String {
    val command = input.str("command")
    if (command.isNotEmpty()) return command
    val path = input.str("file_path")
    if (path.isNotEmpty()) return path
    val query = input.str("query")
    if (query.isNotEmpty()) return query
    if (input.isEmpty()) return name
    return input.entries.joinToString("\n") { (key, value) -> "$key: ${value.render()}" }
}

class SessionTimeline(val sessionId: String) {

    private val items = mutableListOf<TimelineItem>()
    private var contextUsage = ContextUsage()
    private var plan = SessionPlan()
    fun planSnapshot(): SessionPlan = synchronized(this) { plan }
    fun contextSnapshot(): ContextUsage = synchronized(this) { contextUsage }

    private val seenEvents = mutableSetOf<Long>()
    private val resolutions = mutableMapOf<String, String>()
    private val completedRounds = mutableMapOf<String, Long>()
    private val finishedTasks = mutableSetOf<String>()
    private val cancelledTasks = mutableSetOf<String>()
    private val queuedPromptTasks = mutableSetOf<String>()
    private val taskErrors = mutableMapOf<String, String>()
    private val textStream = StringBuilder()
    private var textStreaming = false

    // A reasoning card is one contiguous thinking run. A run opens on the first
    // reasoning frame and closes as soon as the model emits anything else
    // (assistant text, a tool call, approval, task end), so the next thinking
    // segment becomes a new card instead of extending an old one.
    private class ReasoningRun(val id: String) {
        val durable = StringBuilder()
        var delta = StringBuilder()
        var streaming = false
    }

    private val reasoningRuns = mutableMapOf<String, MutableList<ReasoningRun>>()
    private val openReasoningRun = mutableMapOf<String, ReasoningRun>()

    fun snapshot(): List<TimelineItem> = synchronized(this) { items.toList() }

    fun isEmpty(): Boolean = synchronized(this) { items.isEmpty() }

    fun addLocal(item: TimelineItem) {
        synchronized(this) {
            val index = items.indexOfFirst { it.id == item.id }
            if (index >= 0) items[index] = item else items += item
        }
    }

    fun markPrioritized(taskId: String, messageId: String) {
        synchronized(this) {
            val id = userMessageItemId(taskId, messageId)
            val index = items.indexOfFirst { it.id == id }
            val message = items.getOrNull(index) as? TimelineItem.UserText
            if (message != null && message.queued) {
                items[index] = message.copy(prioritized = true)
            }
        }
    }

    /**
     * Verifies queue labels against the server's actual state.
     *
     * `queuedMessageIds` — messages the server currently considers
     * awaiting delivery. Everything else that the client previously displayed as «in queue»
     * has already moved into a task: otherwise, the signature would remain on the screen forever,
     * if a live delivery event was lost during a connection dropout.
     */
    fun syncQueueState(queuedMessageIds: Set<String>) {
        synchronized(this) {
            items.indices.forEach { index ->
                val message = items[index] as? TimelineItem.UserText ?: return@forEach
                if (!message.queued || message.messageId.isEmpty()) return@forEach
                if (message.messageId in queuedMessageIds) return@forEach
                items[index] = message.copy(queued = false, delivered = true)
            }
        }
    }

    /** Does this delivery message wait in the active task's execution phase? */
    fun isMessageQueued(messageId: String): Boolean = synchronized(this) {
        if (messageId.isEmpty()) return false
        items.any { it is TimelineItem.UserText && it.queued && it.messageId == messageId }
    }

    fun seedFromHistory(messages: List<MessageDto>) {
        synchronized(this) {
            if (items.isNotEmpty()) {
                val imported = messages.filter { it.historyId.isNotEmpty() }
                if (imported.isNotEmpty()) {
                    val seed = SessionTimeline(sessionId)
                    seed.seedFromHistory(imported)
                    items.addAll(0, seed.snapshot().filter { candidate -> items.none { it.id == candidate.id } })
                }
                messages.forEachIndexed { index, message ->
                    if (message.role != "assistant" || message.messageId.isNullOrBlank() || message.historyId.isNotEmpty()) return@forEachIndexed
                    val row = TimelineItem.AssistantText(assistantMessageItemId(message.messageId), message.text, files = message.files)
                    val existing = items.indexOfFirst { it.id == row.id }
                    if (existing >= 0) {
                        items[existing] = row
                    } else {
                        val next = messages.drop(index + 1).firstNotNullOfOrNull { following ->
                            val id = when {
                                following.historyId.isNotEmpty() -> following.historyId
                                following.role == "user" && following.taskId != null -> userMessageItemId(following.taskId, following.messageId.orEmpty())
                                following.role == "assistant" && !following.messageId.isNullOrBlank() -> assistantMessageItemId(following.messageId)
                                else -> null
                            }
                            id?.let { key -> items.indexOfFirst { it.id == key }.takeIf { it >= 0 } }
                        }
                        if (next == null) items += row else items.add(next, row)
                    }
                }
                return
            }
            messages.forEachIndexed { index, message ->
                when (message.role) {
                    "user" -> items += TimelineItem.UserText(
                        message.historyId.ifEmpty { message.taskId?.let { userMessageItemId(it, message.messageId.orEmpty()) }
                            ?: "hist-user-$index" },
                        message.text,
                        message.skills,
                        queued = message.queued,
                        taskId = message.taskId.orEmpty(),
                        messageId = message.messageId.orEmpty(),
                    )
                    "assistant" -> items += TimelineItem.AssistantText(
                        message.historyId.ifEmpty { message.messageId?.takeIf { it.isNotBlank() }?.let(::assistantMessageItemId) ?: "hist-assistant-$index" },
                        message.text, files = message.files,
                    )
                    "reasoning" -> items += TimelineItem.Reasoning(message.historyId.ifEmpty { "hist-reasoning-$index" }, message.text)
                    "tool" -> {
                        val id = "import-tool-" + message.toolUseId.ifEmpty { message.historyId.ifEmpty { index.toString() } }
                        val position = items.indexOfFirst { it.id == id }
                        val previous = items.getOrNull(position) as? TimelineItem.ToolCall
                        val input = (message.toolInput as? JsonObject)?.takeIf { it.isNotEmpty() }?.let { formatToolInput(message.toolName, it) }
                            ?: previous?.input ?: ""
                        val updated = TimelineItem.ToolCall(
                            id, message.toolName.ifEmpty { previous?.name ?: "tool" }, input, message.toolStatus,
                            if (message.kind == "tool_result") message.toolOutput.ifEmpty { message.text } else previous?.resultPreview.orEmpty(),
                        )
                        if (position >= 0) items[position] = updated else items += updated
                    }
                }
            }
        }
    }

    fun apply(event: BridgeEvent) {
        val payload = event.payload
        synchronized(this) {
            if (event.sessionId != null && event.sessionId != sessionId) return
            if (event.seq != null && !seenEvents.add(event.seq)) return
            val taskKey = event.taskId ?: payload.str("task_id")
            if (event.kind in setOf("model.text_delta", "model.reasoning_delta") &&
                (taskKey in finishedTasks || payload.long("round") <= (completedRounds[taskKey] ?: -1L))) return
            contextUsage = contextUsage.withEvent(event)
            plan = plan.withEvent(event)
            if (event.kind in REASONING_BOUNDARIES) closeReasoningRunsFor(taskKey)
            when (event.kind) {
                "schedule.delivered" -> {
                    val messageId = payload.str("message_id")
                    val text = payload.str("text")
                    val files = payload.outputFiles()
                    if (messageId.isNotBlank() && (text.isNotBlank() || files.isNotEmpty())) {
                        append(TimelineItem.AssistantText(assistantMessageItemId(messageId), text, files = files))
                    }
                }
                "compaction.started", "compaction.progress", "compaction.completed", "compaction.failed" -> {
                    append(TimelineItem.Compaction(
                        id = "compaction-$taskKey-" + payload.str("compaction_id"),
                        phase = event.kind.substringAfter("."),
                        summary = payload.str("summary_text"),
                        reason = payload.str("reason"),
                        chunk = payload.long("chunk"),
                        totalChunks = payload.long("total_chunks"),
                    ))
                }

                    "task.submitted" -> {
                    val text = payload.str("text")
                    if (text.isNotEmpty()) {
                        upsertLive(
                            TimelineItem.UserText(
                                userMessageItemId(payload.str("task_id"), payload.str("message_id")),
                                text,
                                payload.strList("selected_skills"),
                                taskId = payload.str("task_id"),
                                messageId = payload.str("message_id"),
                            )
                        )
                    }
                }

                // A running task can receive multiple messages, so each row also
                // includes its message ID.
                "task.message_added" -> {
                    val text = payload.str("text")
                    if (text.isNotEmpty()) {
                        upsertLive(
                            TimelineItem.UserText(
                                userMessageItemId(payload.str("task_id"), payload.str("message_id")),
                                text,
                                payload.strList("selected_skills"),
                                queued = true,
                                taskId = payload.str("task_id"),
                                messageId = payload.str("message_id"),
                            )
                        )
                    }
                }

                "task.message_prioritized" -> {
                    val id = userMessageItemId(taskKey, payload.str("message_id"))
                    val index = items.indexOfFirst { it.id == id }
                    val message = items.getOrNull(index) as? TimelineItem.UserText
                    if (message != null) items[index] = message.copy(prioritized = true)
                }

                "task.message_cancelled" -> {
                    val id = userMessageItemId(taskKey, payload.str("message_id"))
                    items.removeAll { it.id == id }
                }

                "steer.delivered" -> {
                    val id = userMessageItemId(taskKey, payload.str("message_id"))
                    val index = items.indexOfFirst { it.id == id }
                    val message = items.getOrNull(index) as? TimelineItem.UserText
                    if (message != null) items[index] = message.copy(queued = false, delivered = true)
                }

                "task.start" -> {
                    if (queuedPromptTasks.remove(taskKey)) markTaskMessagesStarted(taskKey)
                }

                "task.status" -> {
                    val status = payload.str("status")
                    if (taskKey in cancelledTasks && status in FINAL_STATUSES) return
                    if (status == "cancelling") {
                        upsertLive(
                            TimelineItem.Status(
                                "cancel-session",
                                AppLocale.text("Останавливаем задачу…", "Stopping task…"),
                                level = "warn",
                            )
                        )
                        return
                    }
                    if (status in FINAL_STATUSES && payload.str("previous") == "cancelling") {
                        cancelledTasks.add(taskKey)
                        finishedTasks.add(taskKey)
                        remove("cancel-session")
                        upsertLive(
                            TimelineItem.Status(
                                "cancelled-session",
                                AppLocale.text("Задача остановлена", "Task stopped"),
                                level = "ok",
                            )
                        )
                        return
                    }
                    if (status == "waiting_approval") {
                        upsertLive(
                            TimelineItem.Status(
                                "approval-waiting-$taskKey",
                                taskStatusText(status),
                                level = statusLevel(status),
                                taskId = taskKey,
                            )
                        )
                        return
                    }
                    append(
                        TimelineItem.Status(
                            if (status == "ok" && taskKey.isNotEmpty()) "success-$taskKey"
                            else "status-${payload.str("task_id")}-${event.seq ?: status}",
                            taskStatusText(status),
                            level = statusLevel(status),
                            taskId = payload.str("task_id").ifEmpty { taskKey },
                        )
                    )
                }

                "isolation.recovery_started" -> {
                    val attempt = payload.long("attempt").coerceAtLeast(1)
                    val maxAttempts = payload.long("max_attempts").coerceAtLeast(attempt)
                    append(
                        TimelineItem.Status(
                            "recovery-start-$taskKey-${event.seq ?: attempt}",
                            AppLocale.text(
                                "Восстанавливаю выполнение (попытка $attempt из $maxAttempts)",
                                "Recovering task (attempt $attempt of $maxAttempts)",
                            ),
                            level = "warn",
                            taskId = taskKey,
                        )
                    )
                }

                "isolation.recovery_finished" -> {
                    val status = payload.str("status")
                    val message = when (status) {
                        "ok" -> AppLocale.text(
                            "Восстановление завершено, работа продолжилась",
                            "Recovery completed and work continued",
                        )
                        "waiting_approval" -> AppLocale.text(
                            "После восстановления нужно подтверждение",
                            "Recovery resumed and needs your approval",
                        )
                        "fail", "dead_letter", "timeout" -> AppLocale.text(
                            "Автоматическое восстановление не помогло",
                            "Automatic recovery did not resolve the failure",
                        )
                        else -> AppLocale.text(
                            "Восстановление завершилось со статусом: $status",
                            "Recovery finished with status: $status",
                        )
                    }
                    val details = payload.str("error_message").ifBlank {
                        payload.str("failure_reason")
                    }
                    val level = when (status) {
                        "ok" -> "ok"
                        "waiting_approval" -> "warn"
                        "fail", "dead_letter", "timeout" -> "error"
                        else -> "info"
                    }
                    append(
                        TimelineItem.Status(
                            "recovery-finish-$taskKey-${event.seq ?: status}",
                            message,
                            level = level,
                            details = if (level == "error") details else "",
                            taskId = taskKey,
                        )
                    )
                }

                "task.error" -> {
                    val message = payload.str("error_message").trim()
                    if (taskKey.isBlank() || message.isBlank()) return
                    taskErrors[taskKey] = message
                    val index = items.indexOfLast {
                        it is TimelineItem.Status && it.taskId == taskKey && it.level == "error"
                    }
                    if (index >= 0) {
                        val current = items[index] as TimelineItem.Status
                        items[index] = current.copy(details = message)
                    } else {
                        append(
                            TimelineItem.Status(
                                "error-$taskKey",
                                taskStatusText("fail"),
                                level = "error",
                                details = message,
                                taskId = taskKey,
                            )
                        )
                    }
                }

                "model.reasoning_delta" -> {
                    val delta = payload.str("text")
                    if (delta.isEmpty()) return
                    applyReasoningDelta(taskKey, delta)
                }

                "model.reasoning" -> {
                    val text = payload.str("text")
                    if (text.isNotBlank()) applyReasoningChunk(taskKey, text)
                }

                "model.text_delta" -> {
                    val delta = payload.str("text")
                    if (delta.isEmpty()) return
                    textStream.append(delta)
                    textStreaming = true
                    upsertLive(TimelineItem.AssistantText("live-text", textStream.toString(), true))
                }

                "model.round_response" -> {
                    completedRounds[taskKey] = maxOf(completedRounds[taskKey] ?: -1L, payload.long("round"))
                    val text = payload.str("text")
                    textStream.setLength(0)
                    textStreaming = false
                    remove("live-text")
                    if (text.isNotBlank()) {
                        append(TimelineItem.AssistantText("round-${event.seq ?: payload.long("round")}", text))
                    }
                }

                "tool.request_batch" -> {
                    val tools = payload.arr("tools") ?: return
                    tools.forEach { element ->
                        val tool = element as? JsonObject ?: return@forEach
                        val id = "tool-$taskKey-${tool.str("id").ifEmpty { "${event.seq}-${tools.indexOf(element)}" }}"
                        val name = tool.str("name").ifEmpty { "tool" }
                        val input = tool.obj("input") ?: JsonObject(emptyMap())
                        if (items.any { it.id == id }) return@forEach
                        append(TimelineItem.ToolCall(
                            id = id,
                            name = name,
                            input = formatToolInput(name, input),
                            status = "running",
                        ))
                    }
                }

                "tool.result" -> {
                    val id = "tool-$taskKey-${payload.str("tool_use_id") }"
                    val updated = TimelineItem.ToolCall(
                        id = id,
                        name = payload.str("tool_name"),
                        input = formatToolInput(payload.str("tool_name"), payload.obj("tool_input") ?: JsonObject(emptyMap())),
                        status = payload.str("status").ifEmpty { "done" },
                        resultPreview = payload.str("result_preview"),
                        durationMs = payload.long("duration_ms"),
                        storedPath = payload.str("stored_path").ifEmpty { null },
                    )
                    append(updated)
                }

                "approval.required" -> {
                    val requestId = payload.str("request_id")
                    val notice = TimelineItem.ApprovalNotice(
                        id = "approval-$taskKey-$requestId",
                        requestId = requestId,
                        taskId = event.taskId ?: "",
                        tool = payload.str("tool"),
                        target = payload.str("target"),
                        input = payload["input"]?.toString().orEmpty(),
                        reason = payload.str("reason"),
                        dangerous = payload.str("tool").lowercase() in setOf("bash", "write", "edit"),
                        resolved = resolutions["$taskKey:$requestId"],
                    )
                    append(notice)
                    val toolId = "tool-$taskKey-$requestId"
                    val toolIndex = items.indexOfFirst { it.id == toolId }
                    val tool = items.getOrNull(toolIndex) as? TimelineItem.ToolCall
                    if (tool != null) items[toolIndex] = tool.copy(status = "waiting_approval")
                }

                "approval.resolved" -> {
                    val requestId = payload.str("request_id")
                    val decision = payload.str("decision")
                    resolutions["$taskKey:$requestId"] = decision
                    val index = items.indexOfFirst { it.id == "approval-$taskKey-$requestId" }
                    if (index >= 0) {
                        val current = items[index]
                        if (current is TimelineItem.ApprovalNotice) {
                            items[index] = current.copy(resolved = decision)
                        }
                    }
                    releaseToolCard(taskKey, requestId, decision)
                    // Rejection means that the remaining batch tools will not execute—their
                    // cards are closed with a genuine signature of «not completed». Approval
                    // continues work within the same task, so the cards remain active.
                    if (decision == "reject") {
                        settleRunningToolCards(taskKey)
                    } else {
                        reopenSettledToolCards(taskKey)
                    }
                }

                "task.finish" -> {
                    finishedTasks.add(taskKey)
                    remove("approval-waiting-$taskKey")
                    // A completed task cannot keep active cards: tools declared in the batch
                    // that did not receive a result are closed, otherwise an endless flicker of activity
                    // would persist around them. An exception is the pause for approval (`approval_required`):
                    // work will continue in the same task, so the card cannot become «not completed».
                    if (payload.str("reason") != "approval_required") settleRunningToolCards(taskKey)
                    flushStreams()
                    val status = payload.str("status")
                    if (taskKey in cancelledTasks) return
                    if (payload.str("reason") == "interrupted") {
                        cancelledTasks.add(taskKey)
                        remove("cancel-session")
                        upsertLive(
                            TimelineItem.Status(
                                "cancelled-session",
                                AppLocale.text("Задача остановлена", "Task stopped"),
                                level = "ok",
                            )
                        )
                        return
                    }
                    val awaitingApproval =
                        payload.str("reason") == "approval_required" &&
                            payload.long("approval_blocked_count") > 0 &&
                            items.any {
                                it is TimelineItem.ApprovalNotice &&
                                    it.taskId == taskKey &&
                                    it.resolved == null
                            }
                    append(
                        TimelineItem.Status(
                            if (status == "ok" && !awaitingApproval && taskKey.isNotEmpty()) "success-$taskKey"
                            else "finish-${payload.str("task_id")}-${event.seq}",
                            if (awaitingApproval) {
                                AppLocale.text("Работа приостановлена для подтверждения", "Work paused for approval")
                            } else if (status == "ok") {
                                AppLocale.text("Задача завершена", "Task completed")
                            } else {
                                AppLocale.text("Задача завершена со статусом: $status", "Task finished with status: $status")
                            },
                            level = if (awaitingApproval) "warn" else statusLevel(status),
                            details = taskErrors[taskKey].orEmpty(),
                            taskId = payload.str("task_id").ifEmpty { taskKey },
                        )
                    )
                }

                "task.cancel_requested", "session.cancel_requested" -> {
                    upsertLive(
                        TimelineItem.Status(
                            "cancel-session",
                            AppLocale.text(
                                "Запрошена остановка (${payload.str("mode")})",
                                "Stop requested (${payload.str("mode")})",
                            ),
                            level = "warn",
                        )
                    )
                }

                "task.cancelled", "session.cancelled" -> {
                    if (taskKey.isNotEmpty()) {
                        cancelledTasks.add(taskKey)
                        finishedTasks.add(taskKey)
                        settleRunningToolCards(taskKey)
                    }
                    flushStreams()
                    remove("cancel-session")
                    upsertLive(
                        TimelineItem.Status(
                            "cancelled-session",
                            AppLocale.text("Задача остановлена", "Task stopped"),
                            level = "ok",
                        )
                    )
                }

                // The same task resumed with more session messages, so reopen its
                // timeline stream instead of treating it as a new task.
                "task.requeued" -> {
                    if (taskKey.isNotEmpty()) {
                        finishedTasks.remove(taskKey)
                        cancelledTasks.remove(taskKey)
                        completedRounds.remove(taskKey)
                        remove("approval-waiting-$taskKey")
                        if (payload.str("reason") == "pending_steer_messages") {
                            queuedPromptTasks.add(taskKey)
                        }
                    }
                    textStream.setLength(0)
                    textStreaming = false
                    remove("live-text")
                    // Closed reasoning cards survive a requeue; the next thinking
                    // segment opens a new run.
                }

                "stream.ready" -> Unit
                else -> Unit
            }
            Unit
        }
    }

    private fun flushStreams() {
        if (textStreaming) {
            textStream.setLength(0)
            textStreaming = false
            remove("live-text")
        }
    }

    private fun closeReasoningRunsFor(taskKey: String) {
        if (taskKey.isEmpty()) {
            openReasoningRun.keys.toList().forEach(::closeReasoningRun)
        } else {
            closeReasoningRun(taskKey)
        }
    }

    private fun closeReasoningRun(taskKey: String) {
        val run = openReasoningRun.remove(taskKey) ?: return
        run.streaming = false
        renderReasoning(run)
    }

    private fun reasoningRunId(taskKey: String, index: Int): String =
        if (taskKey.isEmpty()) "reasoning-$index" else "reasoning-$taskKey-$index"

    private fun startReasoningRun(taskKey: String): ReasoningRun {
        val runs = reasoningRuns.getOrPut(taskKey) { mutableListOf() }
        val run = ReasoningRun(reasoningRunId(taskKey, runs.size + 1))
        runs += run
        openReasoningRun[taskKey] = run
        return run
    }

    private fun applyReasoningDelta(taskKey: String, delta: String) {
        val run = openReasoningRun[taskKey] ?: startReasoningRun(taskKey)
        run.delta.append(delta)
        run.streaming = true
        renderReasoning(run)
    }

    private fun applyReasoningChunk(taskKey: String, text: String) {
        // A durable chunk extends the open run. When the run was already closed by
        // a live boundary (the durable side often lags live deltas), the chunk
        // still belongs to it as long as it holds an unconfirmed live tail.
        val run = openReasoningRun[taskKey]
            ?: reasoningRuns[taskKey]?.lastOrNull()
                ?.takeIf { it.delta.isNotEmpty() }
            ?: startReasoningRun(taskKey)
        run.durable.append(text)
        run.delta = StringBuilder()
        renderReasoning(run)
    }

    private fun renderReasoning(run: ReasoningRun) {
        val text = run.durable.toString() + run.delta.toString()
        if (text.isBlank()) return
        upsertLive(TimelineItem.Reasoning(run.id, text, run.streaming))
    }

    private fun append(item: TimelineItem) {
        upsertLive(item)
    }

    private fun upsertLive(item: TimelineItem) {
        val index = items.indexOfFirst { it.id == item.id }
        if (index >= 0) items[index] = item else items += item
    }

    private fun remove(id: String) {
        items.removeAll { it.id == id }
    }

    private fun markTaskMessagesStarted(taskKey: String) {
        if (taskKey.isEmpty()) return
        val prefix = "user-$taskKey-"
        items.indices.forEach { index ->
            val message = items[index] as? TimelineItem.UserText ?: return@forEach
            if (message.queued && message.id.startsWith(prefix)) {
                items[index] = message.copy(queued = false)
            }
        }
    }

    fun isResolved(taskId: String, requestId: String): Boolean =
        synchronized(this) { resolutions.containsKey("$taskId:$requestId") }

    private fun releaseToolCard(taskKey: String, requestId: String, decision: String) {
        val index = items.indexOfFirst { it.id == "tool-$taskKey-$requestId" }
        val tool = items.getOrNull(index) as? TimelineItem.ToolCall ?: return
        if (tool.status != "waiting_approval") return
        items[index] = tool.copy(status = if (decision == "reject") "rejected" else "allowed")
    }

    /**
     * Closes the tool cards of this task that remain in the `running` status.
     * The tool card is structurally linked to the task via the identifier prefix
     * `tool-<taskKey>-`, so the completion of one task does not affect the cards of another.
     */
    private fun settleRunningToolCards(taskKey: String) {
        val prefix = "tool-$taskKey-"
        items.forEachIndexed { index, item ->
            if (item is TimelineItem.ToolCall && item.status == "running" && item.id.startsWith(prefix)) {
                items[index] = item.copy(status = "deferred")
            }
        }
    }

    /**
     * Approval continues work within the same task, so cards closed in the
     * previous completion are reactivated: the call result will update them automatically.
     */
    private fun reopenSettledToolCards(taskKey: String) {
        val prefix = "tool-$taskKey-"
        items.forEachIndexed { index, item ->
            if (item is TimelineItem.ToolCall && item.status == "deferred" && item.id.startsWith(prefix)) {
                items[index] = item.copy(status = "running")
            }
        }
    }

    private companion object {
        val FINAL_STATUSES = setOf("ok", "fail", "dead_letter", "cancelled", "timeout")
        val REASONING_BOUNDARIES = setOf(
            "model.text_delta",
            "model.round_response",
            "tool.request_batch",
            "tool.result",
            "approval.required",
            "approval.resolved",
            "task.finish",
            "task.cancelled",
            "session.cancelled",
            "task.requeued",
            "task.cancel_requested",
            "session.cancel_requested",
            "task.error",
        )

        fun taskStatusText(status: String): String = when (status.lowercase()) {
            "queued" -> AppLocale.text("Задача поставлена в очередь", "Task queued")
            "processing", "retry" -> AppLocale.text("Задача выполняется", "Task is running")
            "waiting_approval" -> AppLocale.text("Задача ждёт подтверждения", "Task is waiting for approval")
            "cancelling" -> AppLocale.text("Останавливаем задачу…", "Stopping task…")
            "ok" -> AppLocale.text("Задача завершена", "Task completed")
            "fail" -> AppLocale.text("Задача завершилась с ошибкой", "Task failed")
            "dead_letter", "cancelled" -> AppLocale.text("Задача остановлена", "Task stopped")
            "timeout" -> AppLocale.text("Задача остановлена по таймауту", "Task timed out")
            else -> AppLocale.text("Статус задачи: $status", "Task status: $status")
        }

        fun statusLevel(status: String): String = when (status.lowercase()) {
            "ok" -> "ok"
            "fail", "dead_letter", "timeout" -> "error"
            "cancelled" -> "ok"
            "waiting_approval" -> "warn"
            else -> "info"
        }
    }
}
