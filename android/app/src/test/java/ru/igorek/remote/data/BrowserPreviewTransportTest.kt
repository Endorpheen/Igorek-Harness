package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test

class BrowserPreviewTransportTest {
    private fun serve(body: String, status: Int = 200, block: suspend (BridgeClient, MutableList<String>) -> Unit) {
        val server = BridgeTlsFixture.server(1)
        val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
        val worker = Thread {
            server.accept().use { socket ->
                socket.soTimeout = 5000
                val reader = socket.getInputStream().bufferedReader()
                val path = reader.readLine().split(" ")[1]
                var auth = ""
                var length = 0
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    if (line.startsWith("Authorization:", true)) auth = line.substringAfter(':').trim()
                    if (line.startsWith("Content-Length:", true)) length = line.substringAfter(':').trim().toInt()
                }
                val payload = CharArray(length); var offset = 0
                while (offset < length) { val read = reader.read(payload, offset, length - offset); if (read < 0) break; offset += read }
                requests.add("$path|$auth" + if (length > 0) "|${String(payload)}" else "")
                val bytes = body.toByteArray()
                runCatching { socket.getOutputStream().write("HTTP/1.1 $status OK\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray() + bytes) }
            }
        }.apply { isDaemon = true; start() }
        try { runBlocking { block(BridgeTlsFixture.client(server, "fixture-token"), requests) } }
        finally { server.close(); worker.join(5000) }
    }

    @Test fun `preview authenticates and requests only unchanged frame metadata`() {
        serve("""{"available":true,"ready":true,"unchanged":true,"frame_id":"${"a".repeat(64)}","captured_at":123,"width":640,"height":480}""") { client, requests ->
            val result = client.browserPreview("a".repeat(64))
            assertTrue(result.unchanged);assertEquals("", result.imageBase64)
            assertEquals("/mobile/v1/browser/preview?after=${"a".repeat(64)}|Bearer fixture-token", requests.single())
        }
    }

    @Test fun `preview includes desktop tab list and active tab`() {
        serve("""{"available":true,"ready":false,"reason":"browser_hidden","tabs":[{"id":"web-2","title":"ChatGPT","url":"https://chatgpt.com/"}],"active_tab_id":"web-2"}""") { client, _ ->
            val result = client.browserPreview()
            assertEquals("web-2", result.activeTabId); assertEquals("ChatGPT", result.tabs.single().title)
        }
    }

    @Test fun `tab selection authenticates and sends only explicit desktop tab id`() {
        serve("""{"selected":true,"tab_id":"web-2"}""") { client, requests ->
            assertTrue(client.selectBrowserTab("web-2").selected)
            assertEquals("/mobile/v1/browser/select|Bearer fixture-token|{\"tab_id\":\"web-2\"}", requests.single())
        }
        runBlocking {
            val error = runCatching { BridgeClient("https://127.0.0.1:1", "fixture").selectBrowserTab("../bad") }.exceptionOrNull()
            assertTrue(error is IllegalArgumentException)
        }
    }

    @Test fun `old server produces a recoverable status and huge responses are bounded`() {
        serve("{}",404) { client, _ ->
            val result=runCatching { client.browserPreview() }.exceptionOrNull()
            assertTrue(result is BridgeException);assertEquals(404,(result as BridgeException).status)
        }
        serve("a".repeat(460001)) { client, _ ->
            assertNotNull(runCatching { client.browserPreview() }.exceptionOrNull())
        }
    }

    @Test fun `canceling preview closes its pending network request`() = runBlocking {
        val server=BridgeTlsFixture.server()
        val accepted=CountDownLatch(1);val closed=CountDownLatch(1)
        val worker=Thread {
            server.accept().use { socket ->
                socket.soTimeout=5000
                val reader=socket.getInputStream().bufferedReader()
                while (!reader.readLine().isNullOrEmpty()) { }
                accepted.countDown()
                if(reader.read()==-1)closed.countDown()
            }
        }.apply { isDaemon=true;start() }
        try {
            val request=launch { BridgeTlsFixture.client(server, "fixture-token").browserPreview() }
            withContext(Dispatchers.IO){assertTrue(accepted.await(5,TimeUnit.SECONDS))}
            request.cancelAndJoin()
            withContext(Dispatchers.IO){assertTrue(closed.await(5,TimeUnit.SECONDS))}
        } finally { server.close();worker.join(5000) }
    }
}
