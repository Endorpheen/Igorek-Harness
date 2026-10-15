package ru.igorek.remote.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

internal class TerminalFrames(private val send: (String) -> Boolean) {
    private var sequence = 0L
    var ready = false
        private set

    @Synchronized fun open(ticket: String, rows: Int, cols: Int) {
        ready = false
        sequence = 0
        frame("open", buildJsonObject {
            put("ticket", ticket); put("rows", rows); put("cols", cols); put("since_seq", 0)
        })
    }

    @Synchronized fun received(kind: String) {
        if (kind == "opened") ready = true
        if (kind in setOf("error", "exit", "disconnected")) ready = false
    }

    @Synchronized fun input(kind: String, payload: JsonObject): Boolean {
        if (!ready || kind !in setOf("input", "resize", "ack")) return false
        return frame(kind, payload)
    }

    fun text(value: String): Boolean = input("input", buildJsonObject {
        put("data", java.util.Base64.getEncoder().encodeToString(value.toByteArray(Charsets.UTF_8)))
    })

    private fun frame(kind: String, payload: JsonObject): Boolean = send(buildJsonObject {
        put("kind", kind); put("seq", ++sequence); put("payload", payload)
    }.toString())
}
