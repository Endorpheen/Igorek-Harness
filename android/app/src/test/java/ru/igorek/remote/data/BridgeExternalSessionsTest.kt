package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeExternalSessionsTest {
    private fun serve(responses: List<String>, block: (BridgeClient, MutableList<String>) -> Unit) {
        val server = BridgeTlsFixture.server(1)
        val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
        val worker = Thread {
            for (body in responses) {
                server.accept().use { socket ->
                    socket.soTimeout = 5000
                    val reader = socket.getInputStream().bufferedReader()
                    val requestLine = reader.readLine().split(" ")
                    var contentLength = 0
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                        if (line.startsWith("Content-Length:", ignoreCase = true)) {
                            contentLength = line.substringAfter(':').trim().toInt()
                        }
                    }
                    val requestBody = if (contentLength > 0) {
                        val chars = CharArray(contentLength)
                        var read = 0
                        while (read < contentLength) {
                            val step = reader.read(chars, read, contentLength - read)
                            if (step < 0) break
                            read += step
                        }
                        String(chars, 0, read)
                    } else ""
                    requests.add("${requestLine[1]} ${requestLine[0]} $requestBody".trim())
                    val bytes = body.toByteArray()
                    socket.getOutputStream().use { output ->
                        output.write("HTTP/1.1 200 OK\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
                        output.write(bytes)
                    }
                }
            }
        }.apply { isDaemon = true; start() }
        try {
            block(BridgeTlsFixture.client(server, "test-token"), requests)
        } finally {
            server.close()
            worker.join(5000)
        }
    }

    @Test
    fun `external session listing decodes source counts`() {
        val payload = """
            {"sessions":[{"source":"codex","session_id":"thread-1","title":"Old task",
            "cwd":"/tmp/project","message_count":12,"native_resume":true}],
            "counts":{"claude":2,"opencode":3,"codex":4},"total":9,"scanned_at":100}
        """.trimIndent()
        serve(listOf(payload)) { client, requests ->
            val response = client.externalSessions(refresh = true)

            assertEquals(9, response.total)
            assertEquals(4, response.counts["codex"])
            assertTrue(response.sessions.single().nativeResume)
            assertEquals("/mobile/v1/external-sessions?limit=1000&refresh=1 GET", requests.single())
        }
    }

    @Test
    fun `external session detail decodes normalized messages`() {
        val payload = """
            {"source":"claude","session_id":"session 1","title":"History",
            "messages":[{"role":"user","text":"hello","ts":10,"kind":"text"}],
            "total_messages":1,"next_offset":null}
        """.trimIndent()
        serve(listOf(payload)) { client, requests ->
            val detail = client.externalSession("claude", "session 1")

            assertEquals("hello", detail.messages.single().text)
            assertEquals("/mobile/v1/external-sessions/claude/session%201?offset=0&limit=1000 GET", requests.single())
        }
    }

    @Test
    fun `external import sends profile and working directory`() {
        val payload = """
            {"session":{"session_id":"import-1","meta":{},"messages":[]},
            "imported_messages":2,"skipped_messages":0,"duplicate":false}
        """.trimIndent()
        serve(listOf(payload)) { client, requests ->
            val response = client.importExternalSession(
                "opencode",
                "ses-1",
                "cc-glm",
                "/tmp/project",
                "key-1",
            )

            assertEquals("import-1", response.session.sessionId)
            assertEquals(2, response.importedMessages)
            val request = requests.single()
            assertTrue(request.startsWith("/mobile/v1/external-sessions/opencode/ses-1/import POST "))
            assertTrue(request.contains("\"profile\":\"cc-glm\""))
            assertTrue(request.contains("\"cwd\":\"/tmp/project\""))
        }
    }
}
