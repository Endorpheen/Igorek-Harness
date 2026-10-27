package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test
import java.util.Collections

class PlanMessageWireTest {
    @Test fun `plan flag travels over the real authenticated message request`() {
        val server = BridgeTlsFixture.server(1)
        val bodies = Collections.synchronizedList(mutableListOf<String>())
        val worker = Thread {
            repeat(3) {
                server.accept().use { socket ->
                    socket.soTimeout = 5000
                    val reader = socket.getInputStream().bufferedReader()
                    assertTrue(reader.readLine().startsWith("POST /mobile/v1/sessions/session/messages "))
                    var length = 0
                    var authorized = false
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                        if (line.startsWith("Content-Length:", true)) length = line.substringAfter(':').trim().toInt()
                        if (line.equals("Authorization: Bearer fixture-token", true)) authorized = true
                    }
                    check(authorized)
                    val chars = CharArray(length)
                    var read = 0
                    while (read < chars.size) { val count = reader.read(chars, read, chars.size - read); check(count > 0); read += count }
                    bodies.add(String(chars))
                    val response = """{"task_id":"task-fixture","status":"queued"}""".toByteArray()
                    socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Length: ${response.size}\r\nConnection: close\r\n\r\n").toByteArray() + response)
                    socket.getOutputStream().flush()
                }
            }
        }.apply { isDaemon = true; start() }
        try {
            val client = BridgeTlsFixture.client(server, "fixture-token")
            client.sendMessage("session", "first plan", "plan-key-1", planMode = true)
            client.sendMessage("session", "second plan", "plan-key-2", planMode = true)
            client.sendMessage("session", "normal", "normal-key")
            assertEquals(3, bodies.size)
            assertTrue(Json.parseToJsonElement(bodies[0]).jsonObject["plan_mode"]!!.jsonPrimitive.boolean)
            assertTrue(Json.parseToJsonElement(bodies[1]).jsonObject["plan_mode"]!!.jsonPrimitive.boolean)
            assertFalse(Json.parseToJsonElement(bodies[2]).jsonObject["plan_mode"]!!.jsonPrimitive.boolean)
        } finally { server.close(); worker.join(5000) }
    }
}
