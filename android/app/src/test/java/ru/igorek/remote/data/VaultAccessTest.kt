package ru.igorek.remote.data

import java.util.Collections
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class VaultAccessTest {
    private fun serve(status: Int, body: String, block: (BridgeClient, List<String>) -> Unit) {
        BridgeTlsFixture.server().use { server ->
            server.soTimeout = 5000
            val requests = Collections.synchronizedList(mutableListOf<String>())
            val failures = Collections.synchronizedList(mutableListOf<Throwable>())
            val thread = Thread {
                try {
                    server.accept().use { socket ->
                        socket.soTimeout = 5000
                        val input = socket.getInputStream()
                        fun line(): String {
                            val bytes = mutableListOf<Byte>()
                            while (true) { val value = input.read(); check(value >= 0); if (value == 10) break; bytes += value.toByte() }
                            return bytes.toByteArray().toString(Charsets.UTF_8).trimEnd('\r')
                        }
                        requests += line()
                        var length = 0
                        while (true) {
                            val header = line()
                            if (header.isEmpty()) break
                            if (header.startsWith("Content-Length:", true)) length = header.substringAfter(':').trim().toInt()
                            if (header.startsWith("Authorization:", true)) requests += header
                        }
                        val payload = ByteArray(length)
                        var offset = 0
                        while (offset < length) { val count = input.read(payload, offset, length - offset); check(count > 0); offset += count }
                        requests += payload.toString(Charsets.UTF_8)
                        val bytes = body.toByteArray()
                        socket.getOutputStream().apply {
                            write("HTTP/1.1 $status Fixture\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
                            write(bytes); flush()
                        }
                    }
                } catch (error: Throwable) { failures += error }
            }.apply { isDaemon = true; start() }
            try { block(BridgeTlsFixture.client(server, "retained-fixture-token"), requests) }
            finally { thread.join(6000) }
            assertTrue(failures.toString(), failures.isEmpty())
            assertFalse(thread.isAlive)
        }
    }

    @Test fun `locked state uses existing authenticated pinned connection`() {
        serve(200, """{"state":"locked","supported":true,"unlock_available":true}""") { client, requests ->
            assertTrue(client.vaultStatus().requiresUnlock)
            assertEquals("GET /mobile/v1/vault/status HTTP/1.1", requests[0])
            assertEquals("Authorization: Bearer retained-fixture-token", requests[1])
            assertEquals("", requests[2])
        }
    }

    @Test fun `unlock sends the existing unicode code once and returns unlocked state`() {
        serve(200, """{"state":"unlocked","supported":true,"unlock_available":false}""") { client, requests ->
            assertFalse(client.unlockVault(" fixture-код ").requiresUnlock)
            assertEquals("POST /mobile/v1/vault/unlock HTTP/1.1", requests[0])
            assertEquals(Json.parseToJsonElement("""{"password":" fixture-код "}"""), Json.parseToJsonElement(requests[2]))
            assertEquals(3, requests.size)
        }
    }

    @Test fun `older desktop remains usable without diagnostic errors or re-pairing`() {
        serve(404, """{"error":"not found"}""") { client, _ ->
            var diagnostics = 0
            client.onDiagnostic = { diagnostics++ }
            val access = client.vaultStatus()
            assertFalse(access.supported)
            assertFalse(access.requiresUnlock)
            assertEquals(0, diagnostics)
        }
    }

    @Test fun `wrong code and throttling are distinct from invalid device authentication`() {
        for ((status, code, expected) in listOf(Triple(403, "authentication_failed", "incorrect_code"),
            Triple(429, "unlock_rate_limited", "rate_limited"))) {
            serve(status, """{"error":"$code"}""") { client, requests ->
                val error = assertThrows(BridgeException::class.java) { client.unlockVault("fixture-code") }
                assertEquals(status, error.status)
                assertEquals(expected, vaultUnlockFailure(error))
                assertFalse(error.message.orEmpty().contains("fixture-code"))
                assertEquals(3, requests.size)
            }
        }
    }

    @Test fun `untrusted error details never become displayed unlock errors`() {
        serve(500, """{"error":"fixture-private-code"}""") { client, _ ->
            val error = assertThrows(BridgeException::class.java) { client.unlockVault("fixture-private-code") }
            assertEquals("unavailable", vaultUnlockFailure(error))
            assertFalse(error.toString().contains("fixture-private-code"))
        }
    }

    @Test fun `only a confirmed unlocked state permits the normal UI`() {
        assertFalse(VaultAccess(state = "unlocked").requiresUnlock)
        assertTrue(VaultAccess(state = "locked").requiresUnlock)
        assertTrue(VaultAccess(state = "not_initialized").requiresUnlock)
        assertTrue(VaultAccess(state = "unexpected").requiresUnlock)
        assertFalse(VaultAccess(supported = false).requiresUnlock)
    }

    @Test fun `unlock never reuses an idle connection from before desktop restart`() {
        BridgeTlsFixture.server().use { server ->
            server.soTimeout = 5000
            val failures = Collections.synchronizedList(mutableListOf<Throwable>())
            val thread = Thread {
                try {
                    for ((index, state) in listOf("locked", "unlocked").withIndex()) {
                        server.accept().use { socket ->
                            socket.soTimeout = 5000
                            val input = socket.getInputStream()
                            var ending = ""
                            while (!ending.endsWith("\r\n\r\n")) {
                                val value = input.read(); check(value >= 0); ending += value.toChar()
                            }
                            val length = Regex("(?i)Content-Length: (\\d+)").find(ending)?.groupValues?.get(1)?.toInt() ?: 0
                            repeat(length) { check(input.read() >= 0) }
                            val body = """{"state":"$state","supported":true,"unlock_available":${index == 0}}""".toByteArray()
                            socket.getOutputStream().apply {
                                write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.size}\r\nConnection: keep-alive\r\n\r\n".toByteArray())
                                write(body); flush()
                            }
                            check(input.read() == -1) { "A Vault request left a reusable idle connection" }
                        }
                    }
                } catch (error: Throwable) { failures += error }
            }.apply { isDaemon = true; start() }
            val client = BridgeTlsFixture.client(server, "retained-fixture-token")
            try {
                assertTrue(client.vaultStatus().requiresUnlock)
                assertFalse(client.unlockVault("fixture-code").requiresUnlock)
            } finally { thread.join(6000) }
            assertTrue(failures.toString(), failures.isEmpty())
            assertFalse(thread.isAlive)
        }
    }
}
