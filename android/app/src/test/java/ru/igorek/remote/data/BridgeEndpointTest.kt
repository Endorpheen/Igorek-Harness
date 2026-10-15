package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketTimeoutException

class BridgeEndpointTest {
    @Test fun `manual host defaults to HTTPS and preserves explicit HTTPS`() {
        assertEquals("https://192.168.1.2:47821", secureBridgeUrl(" 192.168.1.2:47821/ "))
        assertEquals("https://localhost:8443/prefix", secureBridgeUrl("https://localhost:8443/prefix/"))
    }

    @Test fun `cleartext and ambiguous endpoints fail before credentials are sent`() {
        listOf("http://127.0.0.1:47821", "HTTP://example.com", "http://[::1]:47821",
            "https://user:password@example.com", "https://example.com?redirect=http://other",
            "https://example.com#fragment", "ftp://example.com").forEach { url ->
            assertThrows(IllegalArgumentException::class.java) { BridgeClient(url, "fixture-token") }
        }
    }

    @Test fun `pair method independently rejects alternate HTTP address`() {
        val client = BridgeClient("https://127.0.0.1:1", "fixture-token")
        assertThrows(IllegalArgumentException::class.java) { client.pair("http://127.0.0.1:1", "ABCD", "test") }
    }

    @Test fun `saved HTTP connection is rejected without rewriting stored credentials`() {
        val encoded = ConnectionCodec.encode(listOf(ServerConnection("saved", "Old", "http://127.0.0.1:47821", "fixture-token")))
        val saved = ConnectionCodec.decode(encoded).single()
        assertThrows(IllegalArgumentException::class.java) { BridgeClient(saved.url, saved.token, saved.fingerprint) }
        assertEquals(encoded, ConnectionCodec.encode(listOf(saved)))
    }

    @Test fun `pairing succeeds over pinned HTTPS`() {
        val server = BridgeTlsFixture.server()
        var request = ""
        val worker = Thread {
            server.accept().use { socket ->
                socket.soTimeout = 5000
                val reader = socket.getInputStream().bufferedReader()
                request = reader.readLine()
                while (!reader.readLine().isNullOrEmpty()) { }
                val body = """{"device_id":"fixture-device","token":"fixture-device-token","name":"test"}"""
                socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: ${body.length}\r\nConnection: close\r\n\r\n$body".toByteArray())
            }
        }.apply { isDaemon = true; start() }
        try {
            val result = BridgeTlsFixture.client(server, "").pair("https://127.0.0.1:${server.localPort}", "ABCD-EF01-2345", "test")
            assertEquals("fixture-device-token", result.token)
            worker.join(5000)
            assertEquals("POST /mobile/v1/pair/complete HTTP/1.1", request)
        } finally { server.close(); worker.join(5000) }
    }

    @Test fun `HTTPS redirect never sends bearer to plaintext listener`() {
        val tls = BridgeTlsFixture.server()
        val plain = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).apply { soTimeout = 500 }
        val worker = Thread {
            tls.accept().use { socket ->
                socket.soTimeout = 5000
                val reader = socket.getInputStream().bufferedReader()
                while (!reader.readLine().isNullOrEmpty()) { }
                socket.getOutputStream().write("HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:${plain.localPort}/mobile/v1/sessions\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
            }
        }.apply { isDaemon = true; start() }
        try {
            assertThrows(BridgeException::class.java) { BridgeTlsFixture.client(tls, "fixture-token").sessionListing() }
            assertThrows(SocketTimeoutException::class.java) { plain.accept().close() }
        } finally { tls.close(); plain.close(); worker.join(5000) }
    }

    @Test fun `wrong fingerprint blocks the HTTPS request`() {
        val server = BridgeTlsFixture.server()
        val worker = Thread {
            runCatching { server.accept().use { socket -> socket.soTimeout = 5000; socket.getInputStream().read() } }
        }.apply { isDaemon = true; start() }
        try {
            val client = BridgeClient("https://127.0.0.1:${server.localPort}", "fixture-token", "0".repeat(64))
            assertThrows(IOException::class.java) { client.sessionListing() }
        } finally { server.close(); worker.join(5000) }
    }
}
