package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class SessionPlanTest {
    private fun event(kind: String, seq: Long, payload: String) = BridgeEvent(
        kind = kind, seq = seq, payload = Json.parseToJsonElement(payload) as kotlinx.serialization.json.JsonObject,
    )

    @Test fun planUpdatesFromRequestsAndResultsAndRejectsOlderHistory() {
        val timeline = SessionTimeline("test")
        timeline.apply(event("tool.request_batch", 1, """{"tools":[{"name":"update_plan","input":{"explanation":"Начинаем","plan":[{"step":"Проверить","status":"in_progress"},{"step":"Исправить","status":"pending"}]}}]}"""))
        assertEquals(2, timeline.planSnapshot().steps.size)
        assertEquals("Начинаем", timeline.planSnapshot().explanation)
        val done = event("tool.result", 3, """{"tool_name":"update_plan","tool_input":{"plan":[{"step":"Проверить","status":"completed"},{"step":"Исправить","status":"in_progress"}]}}""")
        timeline.apply(done)
        timeline.apply(done)
        timeline.apply(event("tool.request_batch", 2, """{"tools":[{"name":"update_plan","input":{"plan":[]}}]}"""))
        assertEquals(listOf("completed", "in_progress"), timeline.planSnapshot().steps.map { it.status })
        assertFalse(SessionTimeline("another").planSnapshot().present)
    }

    @Test fun malformedAndUnrelatedEventsDoNotErasePlanButEmptyPlanDoes() {
        var plan = SessionPlan().withEvent(event("tool.result", 1, """{"tool_name":"update_plan","tool_input":{"plan":[null,{"step":" "},{"step":"  Шаг  ","status":"unknown"}]}}"""))
        assertEquals(listOf(PlanStep("Шаг", "pending")), plan.steps)
        val before = plan
        plan = plan.withEvent(event("tool.result", 2, """{"tool_name":"other","tool_input":{"plan":[]}}"""))
        plan = plan.withEvent(event("tool.result", 3, """{"tool_name":"update_plan","tool_input":{"plan":"broken"}}"""))
        assertEquals(before, plan)
        plan = plan.withEvent(event("tool.result", 4, """{"tool_name":"update_plan","tool_input":{"plan":[]}}"""))
        assertEquals(emptyList<PlanStep>(), plan.steps)
    }
}
