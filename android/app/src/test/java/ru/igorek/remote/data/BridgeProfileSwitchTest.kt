package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class BridgeProfileSwitchTest {
    private fun serve(status: Int, body: String, block: (BridgeClient, MutableList<String>) -> Unit) {
        BridgeTlsFixture.server().use { server ->
            server.soTimeout = 5000
            val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
            val failures = java.util.Collections.synchronizedList(mutableListOf<Throwable>())
            val thread = Thread {
                try {
                    server.accept().use { socket ->
                        socket.soTimeout = 5000
                        val reader = socket.getInputStream().bufferedReader()
                        requests += reader.readLine()
                        var length = 0
                        while (true) {
                            val line = reader.readLine()
                            if (line.isEmpty()) break
                            if (line.startsWith("Content-Length:", true)) length = line.substringAfter(':').trim().toInt()
                            if (line.startsWith("Authorization:", true)) requests += line
                        }
                        val payload = CharArray(length)
                        var read = 0
                        while (read < length) { val count = reader.read(payload, read, length - read); check(count > 0); read += count }
                        requests += String(payload)
                        val bytes = body.toByteArray()
                        socket.getOutputStream().apply {
                            write("HTTP/1.1 $status Fixture\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
                            write(bytes); flush()
                        }
                    }
                } catch (error: Throwable) { failures += error }
            }.apply { isDaemon = true; start() }
            try { block(BridgeTlsFixture.client(server, "fixture-profile-token"), requests) }
            finally { thread.join(6000) }
            assertTrue(failures.toString(), failures.isEmpty())
            assertTrue(!thread.isAlive)
        }
    }

    @Test fun `switch uses the existing authenticated route and preserves returned history`() {
        serve(200, """{"session_id":"chat","meta":{"profile":"local","model":"local-model"},"messages":[{"role":"user","text":"Retained history"}]}""") { client, requests ->
            val response = client.switchSessionProfile("chat", "local")
            assertEquals("chat", response.sessionId)
            assertEquals("local", response.meta["profile"]?.jsonPrimitive?.content)
            assertEquals("Retained history", response.messages.single().text)
            assertEquals("POST /mobile/v1/sessions/chat/profile HTTP/1.1", requests[0])
            assertEquals("Authorization: Bearer fixture-profile-token", requests[1])
            assertEquals("""{"profile":"local"}""", Json.parseToJsonElement(requests[2]).toString())
        }
    }

    @Test fun `busy and unsupported servers return their errors without retrying the write`() {
        for (status in listOf(409, 400, 401)) serve(status, """{"error":"Switch unavailable"}""") { client, requests ->
            val error = assertThrows(BridgeException::class.java) { client.switchSessionProfile("chat", "local") }
            assertEquals(status, error.status)
            assertEquals("Switch unavailable", error.message)
            assertEquals(3, requests.size)
        }
    }

    @Test fun `invalid session paths and blank profiles never reach the network`() {
        val client = BridgeClient("https://127.0.0.1:9", "fixture")
        assertThrows(IllegalArgumentException::class.java) { client.switchSessionProfile("../other", "local") }
        assertThrows(IllegalArgumentException::class.java) { client.switchSessionProfile("chat", " ") }
    }
}
