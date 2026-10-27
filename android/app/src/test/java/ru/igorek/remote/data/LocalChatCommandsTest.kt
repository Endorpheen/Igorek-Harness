package ru.igorek.remote.data

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.*
import org.junit.Test

class LocalChatCommandsTest {
    private val language = AppLocale.current
    @After fun restoreLanguage() { AppLocale.set(language) }

    @Test fun `only exact local commands are intercepted`() {
        assertEquals(LocalChatCommand.Help, parseLocalChatCommand(" /help "))
        assertEquals(LocalChatCommand.Status, parseLocalChatCommand("/status"))
        for (text in listOf("", "/goal test", "/plan", "/plan a task", "/planner", "/HELP", "/fast", "show /status")) {
            assertNull(text, parseLocalChatCommand(text))
        }
    }

    @Test fun `status reports actual session context and available weekly quota`() {
        AppLocale.set(AppLanguage.English)
        val lines = chatStatusLines("session-1", "/work", "model", ContextUsage(window = 1000, lastInput = 300), null, 20.0).toMap()
        assertEquals("session-1", lines["Chat"])
        assertEquals("/work", lines["Directory"])
        assertEquals("model", lines["Model"])
        assertEquals("700 of 1000 tokens remaining", lines["Context"])
        assertEquals("80% remaining", lines["Weekly limit"])
        val limited = chatStatusLines("x", "", "", ContextUsage(lastInput = 300), 200, 120.0).toMap()
        assertEquals("0 of 200 tokens remaining", limited["Context"])
        assertEquals("0% remaining", limited["Weekly limit"])
    }

    @Test fun `unknown or nonfinite measurements are never presented as zero usage`() {
        AppLocale.set(AppLanguage.English)
        val lines = chatStatusLines("x", "", "", ContextUsage(), null, Double.NaN).toMap()
        for (label in listOf("Directory", "Model", "Context", "Weekly limit")) assertEquals("Unavailable", lines[label])
    }

    @Test fun `message wire format explicitly carries plan mode and preserves normal default`() {
        val json = Json { encodeDefaults = true }
        val normal = json.parseToJsonElement(json.encodeToString(SendMessageRequest("inspect"))).jsonObject
        val plan = json.parseToJsonElement(json.encodeToString(SendMessageRequest("inspect", planMode = true))).jsonObject
        assertEquals("false", normal["plan_mode"]!!.jsonPrimitive.content)
        assertEquals("true", plan["plan_mode"]!!.jsonPrimitive.content)
        assertEquals(normal["text"], plan["text"])
    }
}
