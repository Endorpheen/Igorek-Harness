package ru.igorek.remote.data

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class TerminalFramesTest {
    @Test fun completionSendsDraftAndTabWithoutExecutingOrRepeatingDraft() {
        val sent = mutableListOf<JsonObject>()
        val frames = TerminalFrames { sent += Json.parseToJsonElement(it).jsonObject; true }
        assertFalse(frames.text("pri\t"))
        frames.open("ticket", 24, 40)
        frames.received("opened")
        assertTrue(frames.text("pri\t"))
        assertTrue(frames.text("\t"))
        assertTrue(frames.text(" hello\r"))
        val input = sent.drop(1).map {
            String(java.util.Base64.getDecoder().decode(it["payload"]!!.jsonObject["data"]!!.jsonPrimitive.content), Charsets.UTF_8)
        }
        assertEquals(listOf("pri\t", "\t", " hello\r"), input)
        assertFalse(input.first().contains('\r'))
    }

    @Test fun resizeAndKeyboardCannotPrecedeOpenOrShellReadiness() {
        val sent = mutableListOf<JsonObject>()
        val frames = TerminalFrames { sent += Json.parseToJsonElement(it).jsonObject; true }
        val dimensions = buildJsonObject { put("rows", 20); put("cols", 40) }
        assertFalse(frames.input("resize", dimensions))
        frames.open("ticket", 24, 40)
        assertFalse(frames.ready)
        assertFalse(frames.input("input", buildJsonObject { put("data", "Aw==") }))
        frames.received("opened")
        assertTrue(frames.input("resize", dimensions))
        assertEquals(listOf("open", "resize"), sent.map { it["kind"]!!.jsonPrimitive.content })
        assertEquals(listOf(1, 2), sent.map { it["seq"]!!.jsonPrimitive.int })
    }

    @Test fun failureExitAndReconnectRequireFreshShellConfirmation() {
        val sent = mutableListOf<String>()
        val frames = TerminalFrames { sent += it; true }
        for (kind in listOf("error", "exit", "disconnected")) {
            frames.open("ticket", 24, 40)
            frames.received("opened")
            assertTrue(frames.input("ack", buildJsonObject { put("seq", 1) }))
            frames.received(kind)
            assertFalse(frames.ready)
            assertFalse(frames.input("input", buildJsonObject { put("data", "DQ==") }))
        }
        frames.open("fresh", 24, 40)
        assertFalse(frames.ready)
        assertEquals(1, Json.parseToJsonElement(sent.last()).jsonObject["seq"]!!.jsonPrimitive.int)
    }
}
