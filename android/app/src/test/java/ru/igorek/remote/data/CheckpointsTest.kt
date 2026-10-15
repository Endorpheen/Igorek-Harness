package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CheckpointsTest {
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

    @Test
    fun `checkpoints response is parsed from the backend payload`() {
        val raw = """
            {"workdir":"/home/example/repo","available":true,
             "checkpoints":[
               {"hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","short_hash":"aaaaaaa",
                "timestamp":"2026-09-21T09:30:00Z","reason":"checkpoint: before edit"},
               {"hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","short_hash":"bbbbbbb",
                "timestamp":"2026-09-20T22:10:00+03:00","reason":"checkpoint: after tests"}],
             "limit":50,"offset":0,"has_more":true}
        """.trimIndent()
        serve(listOf(raw)) { client, requests ->
            val page = client.sessionCheckpoints("ses-1")

            assertEquals("/mobile/v1/sessions/ses-1/checkpoints?limit=50&offset=0 GET", requests.single().line)
            assertEquals("/home/example/repo", page.workdir)
            assertTrue(page.available)
            assertEquals(50, page.limit)
            assertEquals(0, page.offset)
            assertTrue(page.hasMore)
            assertEquals(2, page.checkpoints.size)
            assertEquals("a".repeat(40), page.checkpoints[0].hash)
            assertEquals("aaaaaaa", page.checkpoints[0].shortHash)
            assertEquals("2026-09-21T09:30:00Z", page.checkpoints[0].timestamp)
            assertEquals("checkpoint: before edit", page.checkpoints[0].reason)
            assertEquals("bbbbbbb", page.checkpoints[1].shortHash)
        }
    }

    @Test
    fun `checkpoints request pages with explicit limit and offset`() {
        serve(listOf("""{"workdir":"/w","available":false,"checkpoints":[]}""")) { client, requests ->
            val page = client.sessionCheckpoints("ses-7", limit = 10, offset = 20)

            assertEquals("/mobile/v1/sessions/ses-7/checkpoints?limit=10&offset=20 GET", requests.single().line)
            assertTrue(page.checkpoints.isEmpty())
            assertFalse(page.available)
            assertFalse(page.hasMore)
        }
    }

    @Test
    fun `missing checkpoint fields fall back to safe defaults`() {
        serve(listOf("""{"checkpoints":[{"hash":"abc"}]}""")) { client, _ ->
            val page = client.sessionCheckpoints("ses-2")
            val checkpoint = page.checkpoints.single()

            assertEquals("abc", checkpoint.hash)
            assertEquals("", checkpoint.shortHash)
            assertEquals("", checkpoint.timestamp)
            assertEquals("", checkpoint.reason)
            assertTrue(page.available)
            assertFalse(page.hasMore)
        }
    }

    @Test
    fun `checkpoint changes are requested by hash and parsed as changed files`() {
        val raw = """
            {"files":[{"path":"factory/core/x.py","additions":12,"deletions":3,"status":"modified"}],
             "totals":{"files":1,"additions":12,"deletions":3}}
        """.trimIndent()
        serve(listOf(raw)) { client, requests ->
            val changes = client.checkpointChanges("ses-1", "a".repeat(40))

            assertEquals("/mobile/v1/sessions/ses-1/checkpoints/${"a".repeat(40)}/changes GET", requests.single().line)
            assertEquals(1, changes.files.size)
            assertEquals("factory/core/x.py", changes.files.single().path)
            assertEquals(12, changes.files.single().additions)
            assertEquals(3, changes.files.single().deletions)
            assertEquals("modified", changes.files.single().status)
            assertEquals(1, changes.totals.files)
            assertEquals(12, changes.totals.additions)
            assertEquals(3, changes.totals.deletions)
        }
    }

    @Test
    fun `checkpoint diff carries the from marker and the encoded path`() {
        val hash = "c".repeat(40)
        val raw = """
            {"path":"factory/core/x.py","additions":4,"deletions":2,"source":"checkpoint",
             "truncated":false,"from":"$hash",
             "hunks":[{"header":"@@ -1,3 +1,4 @@","lines":[
                 {"type":" ","text":"ctx"},
                 {"type":"-","text":"old"},
                 {"type":"+","text":"new"}]}]}
        """.trimIndent()
        serve(listOf(raw)) { client, requests ->
            val diff = client.checkpointDiff("ses-1", hash, "factory/core/x.py")

            assertEquals(
                "/mobile/v1/sessions/ses-1/checkpoints/$hash/diff?path=factory%2Fcore%2Fx.py GET",
                requests.single().line,
            )
            assertEquals("factory/core/x.py", diff.path)
            assertEquals("checkpoint", diff.source)
            assertEquals(hash, diff.from)
            assertEquals("@@ -1,3 +1,4 @@", diff.hunks.single().header)
            assertEquals(listOf(" ", "-", "+"), diff.hunks.single().lines.map { it.type })
        }
    }

    @Test
    fun `old diff payload without from keeps an empty marker`() {
        serve(listOf("""{"path":"a.py","source":"checkpoint"}""")) { client, _ ->
            val diff = client.sessionChangesDiff("ses-3", "a.py")

            assertEquals("a.py", diff.path)
            assertEquals("", diff.from)
        }
    }
}
