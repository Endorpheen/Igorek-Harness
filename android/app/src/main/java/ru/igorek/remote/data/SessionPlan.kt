package ru.igorek.remote.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

data class PlanStep(val text: String, val status: String)

data class SessionPlan(
    val present: Boolean = false,
    val explanation: String = "",
    val steps: List<PlanStep> = emptyList(),
    val seq: Long = 0,
) {
    fun withEvent(event: BridgeEvent): SessionPlan {
        val order = event.seq ?: 0
        if (order > 0 && order < seq) return this
        var result = this
        fun read(input: JsonObject?) {
            val plan = input?.get("plan") as? JsonArray ?: return
            val steps = plan.mapNotNull { item ->
                val obj = item as? JsonObject ?: return@mapNotNull null
                val text = (obj["step"] as? JsonPrimitive)?.contentOrNull?.trim().orEmpty()
                if (text.isEmpty()) return@mapNotNull null
                val status = (obj["status"] as? JsonPrimitive)?.contentOrNull
                PlanStep(text, status?.takeIf { it in setOf("pending", "in_progress", "completed") } ?: "pending")
            }
            result = SessionPlan(true, (input["explanation"] as? JsonPrimitive)?.contentOrNull?.trim().orEmpty(), steps, if (order > 0) order else seq)
        }
        when (event.kind) {
            "tool.request_batch" -> (event.payload["tools"] as? JsonArray)?.forEach { item ->
                val tool = item as? JsonObject ?: return@forEach
                if ((tool["name"] as? JsonPrimitive)?.contentOrNull == "update_plan") read(tool["input"] as? JsonObject)
            }
            "tool.result" -> if ((event.payload["tool_name"] as? JsonPrimitive)?.contentOrNull == "update_plan") {
                read(event.payload["tool_input"] as? JsonObject)
            }
        }
        return result
    }
}
