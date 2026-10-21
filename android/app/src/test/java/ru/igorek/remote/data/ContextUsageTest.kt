package ru.igorek.remote.data

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ContextUsageTest {
    @Test fun percentagesUseLatestRequestIncludingCachedInput() {
        var usage = ContextUsage(window = 200000)
        for (key in listOf("input_tokens", "prompt_tokens", "prompt")) {
            usage = usage.withEvent(BridgeEvent(kind = "model.round_response", payload = buildJsonObject {
                put("usage", buildJsonObject { put(key, 50000); put("cache_read_input_tokens", 40000) })
            }))
            assertEquals(25, usage.usedPercent(usage.window))
        }
        usage = usage.withEvent(BridgeEvent(kind = "model.round_response", payload = buildJsonObject {
            put("usage", buildJsonObject { put("input_tokens", 0) })
        }))
        assertEquals(0, usage.usedPercent(usage.window))
        assertNull(usage.usedPercent(null))
        assertNull(usage.usedPercent(0))
    }
    @Test fun historyReplayDoesNotChangeMetricsOnDuplicateEvents() {
        val timeline = SessionTimeline("test")
        timeline.apply(BridgeEvent(kind = "task.start", seq = 1, payload = buildJsonObject { put("model_context_window", 100000) }))
        val response = BridgeEvent(kind = "model.round_response", seq = 2, payload = buildJsonObject {
            put("usage", buildJsonObject { put("input_tokens", 20000) })
        })
        timeline.apply(response)
        timeline.apply(response)
        assertEquals(ContextUsage(100000, 20000), timeline.contextSnapshot())
    }
    @Test fun localServerWindowOverridesStaleTaskWindow() {
        val usage = ContextUsage(window = 128000, lastInput = 32000)
        assertEquals(64000L, usage.effectiveWindow(64000))
        assertEquals(50, usage.usedPercent(usage.effectiveWindow(64000)))
        assertEquals(25, usage.usedPercent(usage.effectiveWindow(128000)))
        assertEquals(128000L, usage.effectiveWindow(null))
        assertEquals(128000L, usage.effectiveWindow(0))
        assertNull(ContextUsage().usedPercent(ContextUsage().effectiveWindow(64000)))
        assertNull(ContextUsage().effectiveWindow(null))
    }
}
