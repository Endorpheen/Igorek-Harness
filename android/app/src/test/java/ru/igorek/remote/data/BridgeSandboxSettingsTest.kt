package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeSandboxSettingsTest {
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
    fun `sandbox settings are parsed from the backend payload`() {
        val payload = """{"enabled":true,"active":true,"available":true,"reason":""}"""
        serve(listOf(payload)) { client, requests ->
            val settings = client.sandboxSettings()

            assertTrue(settings.enabled)
            assertTrue(settings.active)
            assertTrue(settings.available)
            assertEquals("", settings.reason)
            assertEquals("/mobile/v1/sandbox-settings GET", requests.single().line)
        }
    }

    @Test
    fun `missing sandbox fields fall back to enabled defaults`() {
        serve(listOf("{}")) { client, _ ->
            val settings = client.sandboxSettings()

            assertTrue(settings.enabled)
            assertTrue(settings.active)
            assertTrue(settings.available)
            assertEquals("", settings.reason)
        }
    }

    @Test
    fun `set sandbox enabled patches the flag`() {
        val payload = """{"enabled":false,"active":false,"available":true,"reason":"user_disabled"}"""
        serve(listOf(payload)) { client, requests ->
            val settings = client.setSandboxEnabled(false)

            val request = requests.single()
            assertEquals("/mobile/v1/sandbox-settings PATCH", request.line)
            assertTrue(request.body.contains("\"enabled\":false"))
            assertFalse(settings.enabled)
            assertFalse(settings.active)
            assertTrue(settings.available)
            assertEquals("user_disabled", settings.reason)
        }
    }

    @Test
    fun `switch stays enabled while sandbox is on or bwrap is available`() {
        assertFalse(sandboxSwitchEnabled(null))
        assertFalse(
            sandboxSwitchEnabled(
                SandboxSettingsResponse(
                    enabled = false,
                    active = false,
                    available = false,
                    reason = SANDBOX_REASON_BWRAP_MISSING,
                ),
            ),
        )
        assertTrue(
            sandboxSwitchEnabled(
                SandboxSettingsResponse(
                    enabled = false,
                    active = false,
                    available = true,
                    reason = SANDBOX_REASON_USER_DISABLED,
                ),
            ),
        )
        assertTrue(
            sandboxSwitchEnabled(
                SandboxSettingsResponse(
                    enabled = true,
                    active = false,
                    available = false,
                    reason = SANDBOX_REASON_BWRAP_MISSING,
                ),
            ),
        )
    }

    @Test
    fun `disabling an enabled sandbox requires confirmation`() {
        val enabled = SandboxSettingsResponse()

        assertTrue(sandboxChangeNeedsConfirmation(false, enabled))
        assertFalse(sandboxChangeNeedsConfirmation(true, enabled))
        assertTrue(sandboxChangeNeedsConfirmation(false, null))

        val disabled = SandboxSettingsResponse(
            enabled = false,
            active = false,
            reason = SANDBOX_REASON_USER_DISABLED,
        )
        assertFalse(sandboxChangeNeedsConfirmation(false, disabled))
        assertFalse(sandboxChangeNeedsConfirmation(true, disabled))
    }
}
