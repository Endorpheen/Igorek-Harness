package ru.igorek.remote.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlin.math.roundToInt

data class ContextUsage(val window: Long? = null, val lastInput: Long? = null) {
    fun usedPercent(limit: Long?): Int? = if (limit != null && limit > 0 && lastInput != null)
        (lastInput.toDouble() / limit * 100).roundToInt() else null

    fun withEvent(event: BridgeEvent): ContextUsage {
        fun number(obj: JsonObject, key: String): Long? =
            (obj[key] as? JsonPrimitive)?.longOrNull?.takeIf { it >= 0 }
        return when (event.kind) {
            "task.start" -> copy(window = number(event.payload, "model_context_window")?.takeIf { it > 0 })
            "model.round_response" -> {
                val usage = event.payload["usage"] as? JsonObject ?: return this
                val input = listOf("input_tokens", "prompt_tokens", "prompt")
                    .firstNotNullOfOrNull { number(usage, it) } ?: return this
                copy(lastInput = input)
            }
            else -> this
        }
    }
}
