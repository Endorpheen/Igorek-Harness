package ru.igorek.remote.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val SESSION = "chat-test"

private fun event(
    kind: String,
    seq: Long? = null,
    taskId: String? = "task-1",
    payload: JsonObject = JsonObject(emptyMap()),
) = BridgeEvent(kind = kind, seq = seq, sessionId = SESSION, taskId = taskId, payload = payload)

private fun toolBatch(id: String, name: String, command: String) = event(
    kind = "tool.request_batch",
    seq = 2,
    payload = buildJsonObject {
        put("round", JsonPrimitive(1))
        put(
            "tools",
            buildJsonArray {
                add(
                    buildJsonObject {
                        put("id", JsonPrimitive(id))
                        put("name", JsonPrimitive(name))
                        put("input", buildJsonObject { put("command", JsonPrimitive(command)) })
                    }
                )
            },
        )
    },
)

class SessionTimelineTest {

    @Test
    fun `approval retains canonical input independently of rationale`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("approval.required", payload = buildJsonObject {
            put("request_id", JsonPrimitive("request-fixture"))
            put("tool", JsonPrimitive("Bash"))
            put("reason", JsonPrimitive("Read-only explanation"))
            put("input", buildJsonObject { put("command", JsonPrimitive("rm -rf /workspace/fixture")) })
        }))
        val notice = timeline.snapshot().single() as TimelineItem.ApprovalNotice
        assertTrue(notice.input.contains("rm -rf /workspace/fixture"))
        assertEquals("Read-only explanation", notice.reason)
    }

    @Test
    fun `success events share a card in either order and remain separate across tasks`() {
        listOf(listOf("task.finish", "task.status"), listOf("task.status", "task.finish")).forEach { kinds ->
            val timeline = SessionTimeline(SESSION)
            kinds.forEachIndexed { index, kind ->
                timeline.apply(event(kind, index.toLong() + 1, payload = buildJsonObject {
                    put("status", JsonPrimitive("ok"))
                }))
            }
            timeline.apply(event("task.status", 3, payload = buildJsonObject { put("status", JsonPrimitive("ok")) }))
            assertEquals(1, timeline.snapshot().filterIsInstance<TimelineItem.Status>().size)
            timeline.apply(event("task.finish", 4, taskId = "task-2", payload = buildJsonObject { put("status", JsonPrimitive("ok")) }))
            assertEquals(2, timeline.snapshot().filterIsInstance<TimelineItem.Status>().size)
        }
    }

    @Test
    fun `failed attempt remains visible after success`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.finish", 1, payload = buildJsonObject { put("status", JsonPrimitive("fail")) }))
        timeline.apply(event("task.status", 2, payload = buildJsonObject { put("status", JsonPrimitive("ok")) }))
        assertEquals(listOf("error", "ok"), timeline.snapshot().filterIsInstance<TimelineItem.Status>().map { it.level })
    }

    @Test
    fun importedToolsPairByIdAndPrependAfterNativeReplay() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.round_response", 10, payload = buildJsonObject {
            put("round", JsonPrimitive(1))
            put("text", JsonPrimitive("native"))
        }))
        val messages = listOf(
            MessageDto(role = "user", text = "old", historyId = "u"),
            MessageDto(role = "tool", kind = "tool_call", historyId = "c1", toolUseId = "one", toolName = "Bash", toolInput = buildJsonObject { put("command", JsonPrimitive("pytest")) }),
            MessageDto(role = "tool", kind = "tool_call", historyId = "c2", toolUseId = "two", toolName = "Read"),
            MessageDto(role = "tool", kind = "tool_result", historyId = "r2", toolUseId = "two", toolOutput = "file"),
            MessageDto(role = "tool", kind = "tool_result", historyId = "r1", toolUseId = "one", toolOutput = "3 failed"),
        )
        timeline.seedFromHistory(messages)
        timeline.seedFromHistory(messages)
        val items = timeline.snapshot()
        assertEquals(4, items.size)
        val tools = items.filterIsInstance<TimelineItem.ToolCall>()
        assertEquals("3 failed", tools[0].resultPreview)
        assertEquals("pytest", tools[0].input)
        assertEquals("file", tools[1].resultPreview)
        assertTrue(items.last() is TimelineItem.AssistantText)
    }

    @Test
    fun compactionReplaysFullSummaryWithoutDuplicateCards() {
        val timeline = SessionTimeline(SESSION)
        listOf("started", "progress", "completed").forEachIndexed { index, phase ->
            timeline.apply(event("compaction.$phase", index.toLong() + 1, payload = buildJsonObject {
                put("compaction_id", JsonPrimitive("compact1"))
                put("summary_text", JsonPrimitive("## Цель\n- preserve facts"))
            }))
        }
        val card = timeline.snapshot().single() as TimelineItem.Compaction
        assertEquals("completed", card.phase)
        assertTrue(card.summary.contains("preserve facts"))
    }

    @Test
    fun `queued deltas cannot revive text already restored from journal`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.round_response", 3, payload = buildJsonObject {
            put("round", JsonPrimitive(1))
            put("text", JsonPrimitive("Готово"))
        }))
        timeline.apply(event("model.text_delta", payload = buildJsonObject {
            put("round", JsonPrimitive(1))
            put("text", JsonPrimitive("Го"))
        }))
        assertEquals(1, timeline.snapshot().size)
        timeline.apply(event("model.text_delta", payload = buildJsonObject {
            put("round", JsonPrimitive(2))
            put("text", JsonPrimitive("Новый ответ"))
        }))
        assertEquals(2, timeline.snapshot().size)
    }

    @Test
    fun `submitted message becomes user bubble`() {
        val timeline = SessionTimeline(SESSION)

        timeline.apply(
            event("task.submitted", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("text", JsonPrimitive("проверка связи"))
            })
        )

        val item = timeline.snapshot().single()
        assertEquals(
            TimelineItem.UserText("user-task-1", "проверка связи", taskId = "task-1"),
            item,
        )
    }

    @Test
    fun `approval resolution releases the paired tool card`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_1", "Edit", "file"))
        timeline.apply(event("approval.required", 3, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_1"))
        }))
        assertEquals(
            "waiting_approval",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )

        timeline.apply(event("approval.resolved", 4, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_1"))
            put("decision", JsonPrimitive("allow_once"))
        }))

        assertEquals(
            "allowed",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )
        assertTrue(timeline.isResolved("task-1", "call_1"))
    }

    @Test
    fun `rejected approval marks the paired tool card as rejected`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_2", "Bash", "rm -rf build"))
        timeline.apply(event("approval.required", 3, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_2"))
        }))

        timeline.apply(event("approval.resolved", 4, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_2"))
            put("decision", JsonPrimitive("reject"))
        }))

        assertEquals(
            "rejected",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )
    }

    @Test
    fun `resolved approval does not release the same request of another task`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("approval.required", 1, taskId = "task-1", payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_x"))
        }))
        timeline.apply(event("approval.resolved", 2, taskId = "task-1", payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_x"))
            put("decision", JsonPrimitive("allow_once"))
        }))
        timeline.apply(event("approval.required", 3, taskId = "task-2", payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_x"))
        }))

        val notices = timeline.snapshot().filterIsInstance<TimelineItem.ApprovalNotice>()
        assertEquals(2, notices.size)
        assertEquals("allow_once", notices.first { it.taskId == "task-1" }.resolved)
        assertNull(notices.first { it.taskId == "task-2" }.resolved)
        assertTrue(timeline.isResolved("task-1", "call_x"))
        assertFalse(timeline.isResolved("task-2", "call_x"))
    }

    @Test
    fun `submitted message carries selected skills`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.submitted", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("text", JsonPrimitive("покажи новости"))
                put("selected_skills", buildJsonArray { add(JsonPrimitive("news_rss")) })
            })
        )

        val item = timeline.snapshot().single() as TimelineItem.UserText
        assertEquals("покажи новости", item.text)
        assertEquals(listOf("news_rss"), item.skills)
    }

    @Test
    fun `journal history carries selected skills`() {
        val timeline = SessionTimeline(SESSION)

        timeline.seedFromHistory(
            listOf(MessageDto(role = "user", text = "покажи новости", skills = listOf("news_rss")))
        )

        val item = timeline.snapshot().single() as TimelineItem.UserText
        assertEquals("покажи новости", item.text)
        assertEquals(listOf("news_rss"), item.skills)
    }

    @Test
    fun `journal history restores queued marker and stable message id`() {
        val timeline = SessionTimeline(SESSION)

        timeline.seedFromHistory(
            listOf(
                MessageDto(
                    role = "user",
                    text = "второе сообщение",
                    taskId = "task-1",
                    messageId = "m2",
                    queued = true,
                )
            )
        )

        val item = timeline.snapshot().single() as TimelineItem.UserText
        assertEquals(userMessageItemId("task-1", "m2"), item.id)
        assertTrue(item.queued)
    }

    @Test
    fun `message without skills carries no badges`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.submitted", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("text", JsonPrimitive("обычное сообщение"))
            })
        )

        assertEquals(emptyList<String>(), (timeline.snapshot().single() as TimelineItem.UserText).skills)
    }

    @Test
    fun `local echo and server event do not duplicate`() {
        val timeline = SessionTimeline(SESSION)
        timeline.addLocal(TimelineItem.UserText("user-task-1", "привет"))
        timeline.apply(
            event("task.submitted", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("text", JsonPrimitive("привет"))
            })
        )

        assertEquals(1, timeline.snapshot().size)
    }

    @Test
    fun `message added to running task becomes its own bubble`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.submitted", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m1"))
                put("text", JsonPrimitive("первое сообщение"))
            })
        )
        timeline.apply(
            event("task.message_added", seq = 2, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m2"))
                put("text", JsonPrimitive("второе сообщение"))
            })
        )

        val messages = timeline.snapshot().filterIsInstance<TimelineItem.UserText>()
        assertEquals(listOf("первое сообщение", "второе сообщение"), messages.map { it.text })
        assertFalse(messages[0].queued)
        assertTrue(messages[1].queued)
    }

    @Test
    fun `local echo of steered message keeps its own bubble`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.submitted", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m1"))
                put("text", JsonPrimitive("первое сообщение"))
            })
        )
        // The local echo and server event share an ID, preventing duplicate rows.
        timeline.addLocal(
            TimelineItem.UserText(
                userMessageItemId("task-1", "m2"),
                "второе сообщение",
                queued = true,
            )
        )
        timeline.apply(
            event("task.message_added", seq = 2, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m2"))
                put("text", JsonPrimitive("второе сообщение"))
            })
        )

        val messages = timeline.snapshot().filterIsInstance<TimelineItem.UserText>()
        assertEquals(listOf("первое сообщение", "второе сообщение"), messages.map { it.text })
        assertTrue(messages[1].queued)
    }

    @Test
    fun `delivered steer clears queued marker only for matching message`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.message_added", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m1"))
                put("text", JsonPrimitive("первое уточнение"))
            })
        )
        timeline.apply(
            event("task.message_added", seq = 2, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m2"))
                put("text", JsonPrimitive("второе уточнение"))
            })
        )

        timeline.apply(
            event("steer.delivered", seq = 3, payload = buildJsonObject {
                put("message_id", JsonPrimitive("m1"))
            })
        )

        val messages = timeline.snapshot().filterIsInstance<TimelineItem.UserText>()
        assertFalse(messages.first { it.text == "первое уточнение" }.queued)
        assertTrue(messages.first { it.text == "первое уточнение" }.delivered)
        assertTrue(messages.first { it.text == "второе уточнение" }.queued)
        assertFalse(messages.first { it.text == "второе уточнение" }.delivered)
    }

    @Test
    fun `queue sync marks messages the server no longer waits for as delivered`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.message_added", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m1"))
                put("text", JsonPrimitive("первое уточнение"))
            })
        )
        timeline.apply(
            event("task.message_added", seq = 2, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m2"))
                put("text", JsonPrimitive("второе уточнение"))
            })
        )
        assertTrue(timeline.isMessageQueued("m1"))

        timeline.syncQueueState(setOf("m2"))

        val messages = timeline.snapshot().filterIsInstance<TimelineItem.UserText>()
        assertFalse(messages.first { it.text == "первое уточнение" }.queued)
        assertTrue(messages.first { it.text == "первое уточнение" }.delivered)
        assertTrue(messages.first { it.text == "второе уточнение" }.queued)
        assertFalse(timeline.isMessageQueued("m1"))
        assertTrue(timeline.isMessageQueued("m2"))
    }

    @Test
    fun `task start clears queued markers folded into requeued prompt`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.message_added", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m1"))
                put("text", JsonPrimitive("уточнение"))
            })
        )

        timeline.apply(event("task.requeued", seq = 2, payload = buildJsonObject {
            put("reason", JsonPrimitive("pending_steer_messages"))
        }))
        timeline.apply(event("task.start", seq = 3))

        assertFalse(timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single().queued)
    }

    @Test
    fun `approval resume keeps steer queued until executor delivers it`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.message_added", seq = 1, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("message_id", JsonPrimitive("m1"))
                put("text", JsonPrimitive("уточнение после approve"))
            })
        )
        timeline.apply(event("task.requeued", seq = 2, payload = buildJsonObject {
            put("reason", JsonPrimitive("approval_resolved"))
        }))
        timeline.apply(event("task.start", seq = 3))

        assertTrue(timeline.snapshot().filterIsInstance<TimelineItem.UserText>().single().queued)
    }

    @Test
    fun `requeued task keeps streaming after it was marked finished`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.finish", seq = 1, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
            put("status", JsonPrimitive("ok"))
        }))
        timeline.apply(event("task.requeued", seq = 2, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
        }))
        timeline.apply(event("model.text_delta", seq = 3, payload = buildJsonObject {
            put("round", JsonPrimitive(1))
            put("text", JsonPrimitive("Продолжаю"))
        }))

        assertTrue(
            timeline.snapshot().any {
                it is TimelineItem.AssistantText && it.text == "Продолжаю"
            }
        )
    }

    @Test
    fun `tool call is created running and updated by its result`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_1", "Bash", "ls -la"))

        val running = timeline.snapshot().single() as TimelineItem.ToolCall
        assertEquals("Bash", running.name)
        assertEquals("ls -la", running.input)
        assertEquals("running", running.status)

        timeline.apply(
            event("tool.result", seq = 3, payload = buildJsonObject {
                put("tool_use_id", JsonPrimitive("call_1"))
                put("tool_name", JsonPrimitive("Bash"))
                put("tool_input", buildJsonObject { put("command", JsonPrimitive("ls -la")) })
                put("result_preview", JsonPrimitive("12 passed (exit code: 0)"))
                put("duration_ms", JsonPrimitive(1500))
                put("status", JsonPrimitive("exit"))
            })
        )

        val finished = timeline.snapshot().single() as TimelineItem.ToolCall
        assertEquals("exit", finished.status)
        assertEquals(1500L, finished.durationMs)
        assertTrue(finished.resultPreview.contains("12 passed"))
    }

    @Test
    fun `two tool calls stay as two separate cards in order`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_1", "Bash", "первый"))
        timeline.apply(toolBatch("call_2", "Read", "второй").copy(seq = 3))

        val calls = timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>()
        assertEquals(listOf("tool-task-1-call_1", "tool-task-1-call_2"), calls.map { it.id })
    }

    @Test
    fun `live text deltas accumulate and are replaced by final round text`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.text_delta", payload = buildJsonObject { put("text", JsonPrimitive("Го")) }))
        timeline.apply(event("model.text_delta", payload = buildJsonObject { put("text", JsonPrimitive("тово")) }))

        val streaming = timeline.snapshot().single() as TimelineItem.AssistantText
        assertEquals("Готово", streaming.text)
        assertTrue(streaming.streaming)

        timeline.apply(
            event("model.round_response", seq = 4, payload = buildJsonObject {
                put("text", JsonPrimitive("Готово"))
            })
        )

        val finalized = timeline.snapshot().single() as TimelineItem.AssistantText
        assertEquals("Готово", finalized.text)
        assertTrue(!finalized.streaming)
    }

    @Test
    fun `reasoning deltas are shown until journaled reasoning replaces them`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("Ду")) }))
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("маю")) }))

        val live = timeline.snapshot().single() as TimelineItem.Reasoning
        assertEquals("reasoning-task-1-1", live.id)
        assertEquals("Думаю", live.text)
        assertTrue(live.streaming)

        timeline.apply(
            event("model.reasoning", seq = 5, payload = buildJsonObject { put("text", JsonPrimitive("Думаю")) })
        )

        val items = timeline.snapshot()
        assertEquals(1, items.size)
        val settled = items.single() as TimelineItem.Reasoning
        assertEquals("reasoning-task-1-1", settled.id)
        assertEquals("Думаю", settled.text)
    }

    @Test
    fun `journaled reasoning chunks without a boundary stay one card`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("model.reasoning", seq = 5, payload = buildJsonObject {
                put("text", JsonPrimitive("Первая часть. "))
            })
        )
        timeline.apply(
            event("model.reasoning", seq = 6, payload = buildJsonObject {
                put("text", JsonPrimitive("Вторая часть."))
            })
        )

        val reasoning = timeline.snapshot().single() as TimelineItem.Reasoning
        assertEquals("reasoning-task-1-1", reasoning.id)
        assertEquals("Первая часть. Вторая часть.", reasoning.text)
        assertFalse(reasoning.streaming)
    }

    @Test
    fun `reasoning live deltas keep extending the same run after a flush`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.reasoning_delta", seq = 4, payload = buildJsonObject { put("text", JsonPrimitive("Думаю")) }))
        timeline.apply(
            event("model.reasoning", seq = 5, payload = buildJsonObject { put("text", JsonPrimitive("Думаю")) })
        )
        timeline.apply(event("model.reasoning_delta", seq = 6, payload = buildJsonObject { put("text", JsonPrimitive(" ещё")) }))

        val streaming = timeline.snapshot().single() as TimelineItem.Reasoning
        assertEquals("reasoning-task-1-1", streaming.id)
        assertEquals("Думаю ещё", streaming.text)
        assertTrue(streaming.streaming)

        timeline.apply(
            event("model.reasoning", seq = 7, payload = buildJsonObject { put("text", JsonPrimitive(" ещё")) })
        )

        val finalized = timeline.snapshot().single() as TimelineItem.Reasoning
        assertEquals("Думаю ещё", finalized.text)
    }

    @Test
    fun `reasoning runs stay separate for different tasks`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("model.reasoning", seq = 5, taskId = "task-1", payload = buildJsonObject {
                put("text", JsonPrimitive("первая"))
            })
        )
        timeline.apply(
            event("model.reasoning", seq = 6, taskId = "task-2", payload = buildJsonObject {
                put("text", JsonPrimitive("вторая"))
            })
        )

        val items = timeline.snapshot().filterIsInstance<TimelineItem.Reasoning>()
        assertEquals(listOf("reasoning-task-1-1", "reasoning-task-2-1"), items.map { it.id })
        assertEquals(listOf("первая", "вторая"), items.map { it.text })
    }

    @Test
    fun `assistant text closes a reasoning run and the next thinking opens a new card`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("Первый заход")) }))
        timeline.apply(event("model.text_delta", payload = buildJsonObject { put("text", JsonPrimitive("Ответ")) }))
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("Второй заход")) }))

        val runs = timeline.snapshot().filterIsInstance<TimelineItem.Reasoning>()
        assertEquals(listOf("reasoning-task-1-1", "reasoning-task-1-2"), runs.map { it.id })
        assertEquals(listOf("Первый заход", "Второй заход"), runs.map { it.text })
        assertFalse(runs.first().streaming)
        assertTrue(runs.last().streaming)
    }

    @Test
    fun `tool call closes a reasoning run and the next thinking opens a new card`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("Сначала подумал")) }))
        timeline.apply(toolBatch("call_1", "Bash", "ls"))
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("Подумал ещё")) }))

        val runs = timeline.snapshot().filterIsInstance<TimelineItem.Reasoning>()
        assertEquals(listOf("reasoning-task-1-1", "reasoning-task-1-2"), runs.map { it.id })
        assertEquals(listOf("Сначала подумал", "Подумал ещё"), runs.map { it.text })
    }

    @Test
    fun `late journaled reasoning lands in the run closed by live deltas`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("Думаю")) }))
        timeline.apply(event("model.text_delta", payload = buildJsonObject { put("text", JsonPrimitive("Ответ")) }))
        timeline.apply(
            event("model.reasoning", seq = 5, payload = buildJsonObject { put("text", JsonPrimitive("Думаю")) })
        )

        val runs = timeline.snapshot().filterIsInstance<TimelineItem.Reasoning>()
        assertEquals(1, runs.size)
        assertEquals("reasoning-task-1-1", runs.single().id)
        assertEquals("Думаю", runs.single().text)
    }

    @Test
    fun `requeue closes the current reasoning run`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("До остановки")) }))
        timeline.apply(
            event("task.requeued", seq = 5, payload = buildJsonObject {
                put("reason", JsonPrimitive("pending_steer_messages"))
            })
        )
        timeline.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("После")) }))

        val runs = timeline.snapshot().filterIsInstance<TimelineItem.Reasoning>()
        assertEquals(listOf("reasoning-task-1-1", "reasoning-task-1-2"), runs.map { it.id })
        assertEquals(listOf("До остановки", "После"), runs.map { it.text })
    }

    @Test
    fun `journal history replay yields the same reasoning runs as the live stream`() {
        val history = SessionTimeline(SESSION)
        history.apply(event("model.reasoning", seq = 1, payload = buildJsonObject { put("text", JsonPrimitive("первый")) }))
        history.apply(event("model.reasoning", seq = 2, payload = buildJsonObject { put("text", JsonPrimitive(" заход")) }))
        history.apply(
            event("model.round_response", seq = 3, payload = buildJsonObject {
                put("round", JsonPrimitive(1))
                put("text", JsonPrimitive("ответ"))
            })
        )
        history.apply(event("model.reasoning", seq = 4, payload = buildJsonObject { put("text", JsonPrimitive("второй заход")) }))
        history.apply(toolBatch("call_1", "Bash", "ls").copy(seq = 5))
        history.apply(event("model.reasoning", seq = 6, payload = buildJsonObject { put("text", JsonPrimitive("третий")) }))

        val live = SessionTimeline(SESSION)
        live.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("первый")) }))
        live.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive(" заход")) }))
        live.apply(event("model.reasoning", seq = 1, payload = buildJsonObject { put("text", JsonPrimitive("первый заход")) }))
        live.apply(event("model.text_delta", payload = buildJsonObject { put("text", JsonPrimitive("ответ")) }))
        live.apply(event("model.reasoning", seq = 4, payload = buildJsonObject { put("text", JsonPrimitive("второй заход")) }))
        live.apply(toolBatch("call_1", "Bash", "ls").copy(seq = 5))
        live.apply(event("model.reasoning_delta", payload = buildJsonObject { put("text", JsonPrimitive("третий")) }))
        live.apply(event("model.reasoning", seq = 6, payload = buildJsonObject { put("text", JsonPrimitive("третий")) }))

        fun runs(timeline: SessionTimeline) =
            timeline.snapshot().filterIsInstance<TimelineItem.Reasoning>().map { it.id to it.text }

        val expected = listOf(
            "reasoning-task-1-1" to "первый заход",
            "reasoning-task-1-2" to "второй заход",
            "reasoning-task-1-3" to "третий",
        )
        assertEquals(expected, runs(history))
        assertEquals(expected, runs(live))
    }

    @Test
    fun `approval notice shows request and gets resolved`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("approval.required", seq = 6, payload = buildJsonObject {
                put("request_id", JsonPrimitive("call_9"))
                put("tool", JsonPrimitive("Write"))
                put("target", JsonPrimitive("/tmp/x.txt"))
                put("reason", JsonPrimitive("запись файла"))
            })
        )

        val notice = timeline.snapshot().single() as TimelineItem.ApprovalNotice
        assertEquals("call_9", notice.requestId)
        assertTrue(notice.resolved == null)

        timeline.apply(
            event("approval.resolved", seq = 7, payload = buildJsonObject {
                put("request_id", JsonPrimitive("call_9"))
                put("decision", JsonPrimitive("allow_once"))
            })
        )

        assertEquals("allow_once", (timeline.snapshot().single() as TimelineItem.ApprovalNotice).resolved)
    }

    @Test
    fun `history seeds only into an empty timeline`() {
        val timeline = SessionTimeline(SESSION)
        val history = listOf(
            MessageDto(role = "user", text = "вопрос"),
            MessageDto(role = "assistant", text = "ответ"),
        )

        timeline.seedFromHistory(history)
        assertEquals(2, timeline.snapshot().size)

        timeline.seedFromHistory(history)
        assertEquals(2, timeline.snapshot().size)
    }

    @Test
    fun `task finish is rendered as a status line with level`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(
            event("task.finish", seq = 8, payload = buildJsonObject {
                put("task_id", JsonPrimitive("task-1"))
                put("status", JsonPrimitive("fail"))
            })
        )

        val status = timeline.snapshot().single() as TimelineItem.Status
        assertEquals("error", status.level)
    }

    @Test
    fun `terminal cancellation replaces pending cancellation notice`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.cancel_requested", seq = 8, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
            put("mode", JsonPrimitive("stop"))
        }))
        timeline.apply(event("task.cancelled", seq = 9, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
            put("status", JsonPrimitive("dead_letter"))
        }))

        val status = timeline.snapshot().single() as TimelineItem.Status
        assertEquals("Задача остановлена", status.text)
        assertEquals("ok", status.level)
    }

    @Test
    fun `cancelled task status is rendered as stopped not error`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.status", seq = 8, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
            put("status", JsonPrimitive("cancelled"))
            put("previous", JsonPrimitive("processing"))
        }))

        val status = timeline.snapshot().single() as TimelineItem.Status
        assertEquals("Задача остановлена", status.text)
        assertEquals("ok", status.level)
    }

    @Test
    fun `repeated cancellation requests update one notice`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.cancel_requested", seq = 8, payload = buildJsonObject {
            put("mode", JsonPrimitive("cancel"))
        }))
        timeline.apply(event("task.cancel_requested", seq = 9, payload = buildJsonObject {
            put("mode", JsonPrimitive("stop"))
        }))

        val status = timeline.snapshot().single() as TimelineItem.Status
        assertEquals("Запрошена остановка (stop)", status.text)
    }

    @Test
    fun `interrupted finish is shown as stopped rather than failed`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.finish", seq = 10, payload = buildJsonObject {
            put("status", JsonPrimitive("fail"))
            put("reason", JsonPrimitive("interrupted"))
        }))

        val status = timeline.snapshot().single() as TimelineItem.Status
        assertEquals("Задача остановлена", status.text)
        assertEquals("ok", status.level)
    }

    @Test
    fun `unknown event kinds are ignored`() {
        val timeline = SessionTimeline(SESSION)

        timeline.apply(event("brand.new_event", seq = 9, payload = buildJsonObject { put("x", JsonPrimitive(1)) }))

        assertTrue(timeline.snapshot().isEmpty())
    }

    @Test
    fun `reopening and replaying events preserves unique keys and resolved approval`() {
        val timeline = SessionTimeline(SESSION)
        val events = listOf(
            toolBatch("c1", "Write", "file"),
            event("approval.required", 3, payload = buildJsonObject { put("request_id", JsonPrimitive("c1")) }),
            event("approval.resolved", 4, payload = buildJsonObject {
                put("request_id", JsonPrimitive("c1"))
                put("decision", JsonPrimitive("reject"))
            }),
            event("task.finish", 5, payload = buildJsonObject { put("status", JsonPrimitive("fail")) }),
        )
        repeat(3) { events.forEach(timeline::apply) }
        val items = timeline.snapshot()
        assertEquals(3, items.size)
        assertEquals(items.size, items.map { it.id }.toSet().size)
        assertEquals("reject", items.filterIsInstance<TimelineItem.ApprovalNotice>().single().resolved)
    }

    @Test
    fun `removing streaming text cannot redirect tool result to another card`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("model.text_delta", payload = buildJsonObject { put("text", JsonPrimitive("stream")) }))
        timeline.apply(toolBatch("c1", "Write", "file"))
        timeline.apply(event("model.round_response", 3, payload = buildJsonObject { put("text", JsonPrimitive("final")) }))
        timeline.apply(event("tool.result", 4, payload = buildJsonObject {
            put("tool_use_id", JsonPrimitive("c1"))
            put("status", JsonPrimitive("done"))
        }))
        assertEquals(2, timeline.snapshot().size)
        assertEquals("done", timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status)
        assertEquals("final", timeline.snapshot().filterIsInstance<TimelineItem.AssistantText>().single().text)
    }

    @Test
    fun `tool identifiers reused by resumed tasks do not collide`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("c1", "Write", "file"))
        timeline.apply(toolBatch("c1", "Write", "file").copy(taskId = "task-2", seq = 3))
        assertEquals(2, timeline.snapshot().map { it.id }.toSet().size)
    }

    @Test
    fun `resolution arriving before history stays resolved`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("approval.resolved", 4, payload = buildJsonObject {
            put("request_id", JsonPrimitive("c1"))
            put("decision", JsonPrimitive("allow_once"))
        }))
        timeline.apply(event("approval.required", 3, payload = buildJsonObject { put("request_id", JsonPrimitive("c1")) }))
        assertEquals("allow_once", (timeline.snapshot().single() as TimelineItem.ApprovalNotice).resolved)
    }

    @Test
    fun `approval pause is not presented as failed work`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("approval.required", 3, payload = buildJsonObject { put("request_id", JsonPrimitive("c1")) }))
        timeline.apply(event("task.finish", 4, payload = buildJsonObject {
            put("status", JsonPrimitive("fail"))
            put("reason", JsonPrimitive("approval_required"))
            put("approval_blocked_count", JsonPrimitive(1))
        }))
        assertEquals("warn", timeline.snapshot().filterIsInstance<TimelineItem.Status>().single().level)
    }

    @Test
    fun `resolved approval followed by successful finish does not leave a false pause banner`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("approval.required", 1, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_1"))
        }))
        timeline.apply(event("task.status", 2, payload = buildJsonObject {
            put("status", JsonPrimitive("waiting_approval"))
        }))
        timeline.apply(event("approval.resolved", 3, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_1"))
            put("decision", JsonPrimitive("allow_once"))
        }))
        timeline.apply(event("task.requeued", 4, payload = buildJsonObject {
            put("reason", JsonPrimitive("approval_resolved"))
        }))
        timeline.apply(event("task.finish", 5, payload = buildJsonObject {
            put("status", JsonPrimitive("ok"))
            put("reason", JsonPrimitive(""))
            put("approval_blocked_count", JsonPrimitive(0))
        }))

        val statuses = timeline.snapshot().filterIsInstance<TimelineItem.Status>()
        assertEquals(1, statuses.size)
        assertEquals("Задача завершена", statuses.single().text)
        assertEquals("ok", statuses.single().level)
    }

    @Test
    fun `task finish without pending approval does not show pause even with historical notice`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("approval.required", 1, payload = buildJsonObject {
            put("request_id", JsonPrimitive("call_1"))
        }))
        timeline.apply(event("task.finish", 2, payload = buildJsonObject {
            put("status", JsonPrimitive("fail"))
            put("reason", JsonPrimitive("http_error"))
            put("approval_blocked_count", JsonPrimitive(0))
        }))

        assertEquals(
            "Задача завершена со статусом: fail",
            timeline.snapshot().filterIsInstance<TimelineItem.Status>().single().text,
        )
    }

    @Test
    fun `isolation recovery progress and outcome are shown in the timeline`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("isolation.recovery_started", 1, payload = buildJsonObject {
            put("attempt", JsonPrimitive(1))
            put("max_attempts", JsonPrimitive(1))
            put("reason", JsonPrimitive("approval_request_missing"))
        }))
        timeline.apply(event("isolation.recovery_finished", 2, payload = buildJsonObject {
            put("attempt", JsonPrimitive(1))
            put("status", JsonPrimitive("fail"))
            put("failure_reason", JsonPrimitive("approval_request_missing"))
            put("error_message", JsonPrimitive("Approval recovery failed"))
        }))

        val statuses = timeline.snapshot().filterIsInstance<TimelineItem.Status>()
        assertEquals(2, statuses.size)
        assertEquals("warn", statuses.first().level)
        assertTrue(
            statuses.first().text.contains("попытка", ignoreCase = true) ||
                statuses.first().text.contains("attempt", ignoreCase = true),
        )
        assertEquals("error", statuses.last().level)
        assertEquals("Approval recovery failed", statuses.last().details)
    }

    @Test
    fun `task error details attach to the red failure card`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(event("task.status", 1, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
            put("status", JsonPrimitive("fail"))
        }))
        timeline.apply(event("task.error", 2, payload = buildJsonObject {
            put("task_id", JsonPrimitive("task-1"))
            put("error_message", JsonPrimitive("Recovery stopped after one attempt"))
        }))

        val failure = timeline.snapshot().filterIsInstance<TimelineItem.Status>().single()
        assertEquals("error", failure.level)
        assertEquals("task-1", failure.taskId)
        assertEquals("Recovery stopped after one attempt", failure.details)
    }

    @Test
    fun `approval pause keeps batch neighbours active instead of marking them not executed`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_done", "Bash", "ls -la"))
        timeline.apply(
            event("tool.result", seq = 3, payload = buildJsonObject {
                put("tool_use_id", JsonPrimitive("call_done"))
                put("tool_name", JsonPrimitive("Bash"))
                put("status", JsonPrimitive("done"))
            })
        )
        // Batch tail: the tool is blocked by an approval request, and its neighbor did not execute.
        timeline.apply(toolBatch("call_blocked", "Bash", "rm -rf build").copy(seq = 4))
        timeline.apply(toolBatch("call_tail", "Read", "file").copy(seq = 5))
        timeline.apply(
            event("approval.required", 6, payload = buildJsonObject { put("request_id", JsonPrimitive("call_blocked")) })
        )
        timeline.apply(
            event("task.finish", 7, payload = buildJsonObject {
                put("status", JsonPrimitive("fail"))
                put("reason", JsonPrimitive("approval_required"))
            })
        )

        val calls = timeline.snapshot()
            .filterIsInstance<TimelineItem.ToolCall>()
            .associateBy { it.id }
        assertEquals("done", calls.getValue("tool-task-1-call_done").status)
        assertEquals("waiting_approval", calls.getValue("tool-task-1-call_blocked").status)
        // Pause for confirmation is not task completion: the neighbor may still execute in the same task after a positive decision, so it is impossible to mark it as 'not executed'.
        assertEquals("running", calls.getValue("tool-task-1-call_tail").status)
    }

    @Test
    fun `task finish without approval pause closes leftovers as not executed`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_tail", "Read", "file"))
        timeline.apply(
            event("task.finish", 3, payload = buildJsonObject { put("status", JsonPrimitive("ok")) })
        )
        assertEquals(
            "deferred",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )
    }

    @Test
    fun `reject closes leftovers as not executed`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_blocked", "Bash", "rm -rf build"))
        timeline.apply(toolBatch("call_tail", "Read", "file").copy(seq = 3))
        timeline.apply(
            event("approval.required", 4, payload = buildJsonObject { put("request_id", JsonPrimitive("call_blocked")) })
        )
        timeline.apply(
            event("task.finish", 5, payload = buildJsonObject {
                put("status", JsonPrimitive("fail"))
                put("reason", JsonPrimitive("approval_required"))
            })
        )
        timeline.apply(
            event("approval.resolved", 6, payload = buildJsonObject {
                put("request_id", JsonPrimitive("call_blocked"))
                put("decision", JsonPrimitive("reject"))
            })
        )

        val calls = timeline.snapshot()
            .filterIsInstance<TimelineItem.ToolCall>()
            .associateBy { it.id }
        assertEquals("rejected", calls.getValue("tool-task-1-call_blocked").status)
        assertEquals("deferred", calls.getValue("tool-task-1-call_tail").status)
    }

    @Test
    fun `positive decision keeps the card up to the submitted result`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_blocked", "Bash", "rm -rf build"))
        timeline.apply(
            event("approval.required", 3, payload = buildJsonObject { put("request_id", JsonPrimitive("call_blocked")) })
        )
        timeline.apply(
            event("task.finish", 4, payload = buildJsonObject {
                put("status", JsonPrimitive("fail"))
                put("reason", JsonPrimitive("approval_required"))
            })
        )
        timeline.apply(
            event("approval.resolved", 5, payload = buildJsonObject {
                put("request_id", JsonPrimitive("call_blocked"))
                put("decision", JsonPrimitive("allow_session"))
                put("resumed_task_id", JsonPrimitive("task-1"))
            })
        )
        assertEquals(
            "allowed",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )

        // A confirmed call executes within the same task—the card becomes 'ready,' not remaining 'not executed'.
        timeline.apply(
            event("tool.result", 6, payload = buildJsonObject {
                put("tool_use_id", JsonPrimitive("call_blocked"))
                put("tool_name", JsonPrimitive("Bash"))
                put("status", JsonPrimitive("done"))
            })
        )
        val calls = timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>()
        assertEquals(1, calls.size)
        assertEquals("done", calls.single().status)
    }

    @Test
    fun `running tool card stays active while its task is still going`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_1", "Bash", "sleep 60"))
        assertEquals(
            "running",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )
    }

    @Test
    fun `finish of another task does not close running tool cards`() {
        val timeline = SessionTimeline(SESSION)
        timeline.apply(toolBatch("call_1", "Bash", "sleep 60"))
        timeline.apply(
            event("task.finish", 3, taskId = "task-2", payload = buildJsonObject { put("status", JsonPrimitive("ok")) })
        )
        assertEquals(
            "running",
            timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single().status,
        )
    }
}
