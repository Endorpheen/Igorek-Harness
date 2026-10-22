package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeSessionPinsTest {
    private fun serve(status: Int, body: String, block: (BridgeClient, MutableList<String>) -> Unit) {
        BridgeTlsFixture.server().use { server ->
            server.soTimeout = 5000
            val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
            val failures = java.util.Collections.synchronizedList(mutableListOf<Throwable>())
            val worker = Thread {
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
                        val chars = CharArray(length)
                        var read = 0
                        while (read < length) { val count = reader.read(chars, read, length - read); check(count > 0); read += count }
                        requests += String(chars)
                        val bytes = body.toByteArray()
                        socket.getOutputStream().apply {
                            write("HTTP/1.1 $status Fixture\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
                            write(bytes); flush()
                        }
                    }
                } catch (error: Throwable) { failures += error }
            }.apply { isDaemon = true; start() }
            try { block(BridgeTlsFixture.client(server, "fixture-session-pins"), requests) }
            finally { worker.join(6000) }
            assertTrue(failures.toString(), failures.isEmpty())
            assertFalse(worker.isAlive)
        }
    }

    @Test fun `pin and unpin use authenticated PATCH without changing archive or title`() {
        for (pinned in listOf(true, false)) serve(200, """{"session_id":"chat","pinned":$pinned}""") { client, requests ->
            client.updateSession("chat", pinned = pinned)
            assertEquals("PATCH /mobile/v1/sessions/chat HTTP/1.1", requests[0])
            assertEquals("Authorization: Bearer fixture-session-pins", requests[1])
            assertEquals("""{"pinned":$pinned}""", Json.parseToJsonElement(requests[2]).toString())
        }
    }

    @Test fun `listing retains three pins and the unpinned active session`() {
        serve(200, """{"sessions":[{"session_id":"p1","pinned":true},{"session_id":"p2","pinned":true},{"session_id":"p3","pinned":true},{"session_id":"active","active":true}]}""") { client, requests ->
            val rows = client.sessions()
            assertEquals(3, rows.count { it.pinned })
            assertTrue(rows.last().active)
            assertFalse(rows.last().pinned)
            assertEquals("GET /mobile/v1/sessions HTTP/1.1", requests[0])
        }
    }

    @Test fun `rejected pin surfaces the server error and never retries the write`() {
        for (status in listOf(400, 401, 409, 500)) serve(status, """{"error":"Pin unavailable"}""") { client, requests ->
            val error = assertThrows(BridgeException::class.java) { client.updateSession("chat", pinned = true) }
            assertEquals(status, error.status)
            assertEquals("Pin unavailable", error.message)
            assertEquals(3, requests.size)
        }
    }
}
