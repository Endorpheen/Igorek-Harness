package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeSecuritySettingsTest {
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
    fun `security settings are parsed from the backend payload`() {
        val payload = """
            {"level":"trusted","baseline":"standard","is_weakened":true,"expires_at":123,
             "available_levels":["strict","standard","trusted","full"]}
        """.trimIndent()
        serve(listOf(payload)) { client, requests ->
            val settings = client.securitySettings()

            assertEquals("trusted", settings.level)
            assertEquals("standard", settings.baselineLevel)
            assertTrue(settings.isWeakened)
            assertEquals(123L, settings.expiresAt)
            assertEquals(listOf("strict", "standard", "trusted", "full"), settings.availableLevels)
            assertEquals("/mobile/v1/security-settings GET", requests.single().line)
        }
    }

    @Test
    fun `baseline_level alias is accepted`() {
        val payload = """
            {"level":"trusted","baseline_level":"standard","is_weakened":true,"expires_at":123,
             "available_levels":["strict","standard","trusted","full"]}
        """.trimIndent()
        serve(listOf(payload)) { client, _ ->
            val settings = client.securitySettings()

            assertEquals("standard", settings.baselineLevel)
            assertEquals("trusted", settings.level)
            assertTrue(settings.isWeakened)
        }
    }

    @Test
    fun `set security level patches level ttl and confirm`() {
        val payload = """
            {"level":"full","baseline":"standard","is_weakened":true,"expires_at":3700,
             "available_levels":["strict","standard","trusted","full"]}
        """.trimIndent()
        serve(listOf(payload)) { client, requests ->
            val settings = client.setSecurityLevel("full", ttlS = 3600, confirm = true)

            val request = requests.single()
            assertEquals("/mobile/v1/security-settings PATCH", request.line)
            assertTrue(request.body.contains("\"level\":\"full\""))
            assertTrue(request.body.contains("\"ttl_s\":3600"))
            assertTrue(request.body.contains("\"confirm\":true"))
            assertEquals("full", settings.level)
            assertTrue(settings.isWeakened)
            assertEquals(3700L, settings.expiresAt)
        }
    }

    @Test
    fun `standard level shows no badge and full level shows it`() {
        val standard = SecuritySettingsResponse(level = "standard", baselineLevel = "standard")
        val full = SecuritySettingsResponse(
            level = "full",
            baselineLevel = "standard",
            isWeakened = true,
            expiresAt = 2_000L,
        )

        assertFalse(securityBadgeVisible(standard, nowSeconds = 1_000))
        assertTrue(securityBadgeVisible(full, nowSeconds = 1_000))
        // The server did not set a flag, but the level is weaker than the base level — badge is mandatory.
        assertTrue(securityBadgeVisible(full.copy(isWeakened = false), nowSeconds = 1_000))
        // After the expiration period, weakening is invalid — no badge, base level.
        assertFalse(securityBadgeVisible(full, nowSeconds = 3_000))
        assertEquals("standard", securityEffectiveLevel(full, nowSeconds = 3_000))
        assertEquals("full", securityEffectiveLevel(full, nowSeconds = 1_000))
    }

    @Test
    fun `unknown level falls back to standard without crashing`() {
        assertEquals("standard", normalizeSecurityLevel("root"))
        assertEquals("standard", securityEffectiveLevel(SecuritySettingsResponse(level = "root"), nowSeconds = 0))

        val options = securityLevelOptions(SecuritySettingsResponse(availableLevels = listOf("root")))
        assertEquals(SECURITY_LEVEL_ORDER, options)

        val known = SecuritySettingsResponse(availableLevels = listOf("full", "unknown", "standard", "full"))
        assertEquals(listOf("full", "standard"), securityLevelOptions(known))
    }

    @Test
    fun `weakening requires confirmation before patch`() {
        val standard = SecuritySettingsResponse(level = "standard", baselineLevel = "standard")

        assertFalse(securityChangeNeedsConfirmation("strict", standard))
        assertFalse(securityChangeNeedsConfirmation("standard", standard))
        assertTrue(securityChangeNeedsConfirmation("trusted", standard))
        assertTrue(securityChangeNeedsConfirmation("full", standard))
        // Settings are not yet loaded — weakening still requires confirmation.
        assertTrue(securityChangeNeedsConfirmation("full", null))

        val weakened = SecuritySettingsResponse(
            level = "full",
            baselineLevel = "standard",
            isWeakened = true,
            expiresAt = 999L,
        )
        // Return to the base level — reinforcement, no dialogue required.
        assertFalse(securityChangeNeedsConfirmation("standard", weakened))
    }

    @Test
    fun `expiry reports minutes left and expiration`() {
        assertEquals(SecurityExpiry(minutesLeft = 3, expired = false), securityExpiry(180, 0))
        assertEquals(SecurityExpiry(minutesLeft = 1, expired = false), securityExpiry(1, 0))
        assertTrue(securityExpiry(10, 11)!!.expired)
        assertNull(securityExpiry(null, 0))
    }
}
