package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionChangesTest {
    private class RecordedRequest(val line: String, val body: String)

    private fun serve(
        responses: List<String>,
        block: (BridgeClient, MutableList<RecordedRequest>) -> Unit,
    ) {
        val server = BridgeTlsFixture.server(1)
        val requests = java.util.Collections.synchronizedList(mutableListOf<RecordedRequest>())
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
                    var payload = ""
                    if (contentLength > 0) {
                        val chars = CharArray(contentLength)
                        var read = 0
                        while (read < contentLength) {
                            val step = reader.read(chars, read, contentLength - read)
                            if (step < 0) break
                            read += step
                        }
                        payload = String(chars, 0, read)
                    }
                    requests.add(RecordedRequest("${requestLine[1]} ${requestLine[0]}", payload))
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

    private fun payload(raw: String): JsonObject = Json.parseToJsonElement(raw).jsonObject

    @Test
    fun `changed files response is parsed from the backend payload`() {
        val raw = """
            {"files":[{"path":"factory/core/x.py","additions":12,"deletions":3,"status":"modified"},
                      {"path":"factory/core/new.py","additions":30,"deletions":4,"status":"added"}],
             "totals":{"files":2,"additions":42,"deletions":7}}
        """.trimIndent()
        serve(listOf(raw)) { client, requests ->
            val changes = client.sessionChanges("ses-1")

            assertEquals("/mobile/v1/sessions/ses-1/changes GET", requests.single().line)
            assertEquals(2, changes.files.size)
            assertEquals("factory/core/x.py", changes.files[0].path)
            assertEquals(12, changes.files[0].additions)
            assertEquals(3, changes.files[0].deletions)
            assertEquals("modified", changes.files[0].status)
            assertEquals("factory/core/new.py", changes.files[1].path)
            assertEquals("added", changes.files[1].status)
            assertEquals(2, changes.totals.files)
            assertEquals(42, changes.totals.additions)
            assertEquals(7, changes.totals.deletions)
        }
    }

    @Test
    fun `empty payload yields an empty change list`() {
        val raw = """{"files":[],"totals":{"files":0,"additions":0,"deletions":0}}"""
        serve(listOf(raw)) { client, _ ->
            val changes = client.sessionChanges("ses-empty")

            assertTrue(changes.files.isEmpty())
            assertEquals(0, changes.totals.files)
            assertEquals(0, changes.totals.additions)
            assertEquals(0, changes.totals.deletions)
        }
    }

    @Test
    fun `missing per-file fields fall back to safe defaults`() {
        serve(listOf("""{"files":[{"path":"a.py"}]}""")) { client, _ ->
            val changes = client.sessionChanges("ses-2")
            val file = changes.files.single()

            assertEquals("a.py", file.path)
            assertEquals(0, file.additions)
            assertEquals(0, file.deletions)
            assertEquals("modified", file.status)
            assertEquals(0, changes.totals.files)
            assertEquals(0, changes.totals.additions)
            assertEquals(0, changes.totals.deletions)
        }
    }

    @Test
    fun `file diff response is parsed from the backend payload`() {
        val raw = """
            {"path":"factory/core/x.py","additions":4,"deletions":2,"source":"checkpoint","truncated":true,
             "hunks":[{"header":"@@ -1,3 +1,4 @@","lines":[
                 {"type":" ","text":"ctx"},
                 {"type":"-","text":"old"},
                 {"type":"+","text":"new"}]}]}
        """.trimIndent()
        serve(listOf(raw)) { client, requests ->
            val diff = client.sessionChangesDiff("ses-1", "factory/core/x.py")

            assertEquals("/mobile/v1/sessions/ses-1/changes/diff?path=factory%2Fcore%2Fx.py GET", requests.single().line)
            assertEquals("factory/core/x.py", diff.path)
            assertEquals(4, diff.additions)
            assertEquals(2, diff.deletions)
            assertEquals("checkpoint", diff.source)
            assertTrue(diff.truncated)
            val hunk = diff.hunks.single()
            assertEquals("@@ -1,3 +1,4 @@", hunk.header)
            assertEquals(listOf(" ", "-", "+"), hunk.lines.map { it.type })
            assertEquals(listOf("ctx", "old", "new"), hunk.lines.map { it.text })
        }
    }

    @Test
    fun `file diff without optional fields falls back to unavailable`() {
        serve(listOf("""{"path":"a.py"}""")) { client, _ ->
            val diff = client.sessionChangesDiff("ses-3", "a.py")

            assertEquals("a.py", diff.path)
            assertEquals(0, diff.additions)
            assertEquals(0, diff.deletions)
            assertEquals("unavailable", diff.source)
            assertFalse(diff.truncated)
            assertTrue(diff.hunks.isEmpty())
        }
    }

    @Test
    fun `file write tool events are detected for results and batches`() {
        assertTrue(isFileWriteToolEvent("tool.result", payload("""{"tool_name":"write","status":"done"}""")))
        assertTrue(isFileWriteToolEvent("tool.result", payload("""{"tool_name":"Edit"}""")))
        assertTrue(isFileWriteToolEvent("tool.result", payload("""{"tool_name":"tools.apply_patch"}""")))
        assertFalse(isFileWriteToolEvent("tool.result", payload("""{"tool_name":"read"}""")))
        assertFalse(isFileWriteToolEvent("tool.result", payload("""{"tool_name":""}""")))
        assertFalse(isFileWriteToolEvent("model.text_delta", payload("""{"tool_name":"write"}""")))

        val batch = payload("""{"tools":[{"id":"1","name":"read"},{"id":"2","name":"edit"}]}""")
        assertTrue(isFileWriteToolEvent("tool.request_batch", batch))
        assertFalse(isFileWriteToolEvent("tool.request_batch", payload("""{"tools":[{"name":"bash"}]}""")))
        assertFalse(isFileWriteToolEvent("tool.request_batch", payload("""{"tools":[]}""")))
        assertFalse(isFileWriteToolEvent("tool.request_batch", payload("""{}""")))
    }
}
