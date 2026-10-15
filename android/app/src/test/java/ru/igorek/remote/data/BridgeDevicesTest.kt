package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import org.junit.Assert.assertEquals
import org.junit.Test

class BridgeDevicesTest {
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
                    if (contentLength > 0) {
                        val chars = CharArray(contentLength)
                        var read = 0
                        while (read < contentLength) {
                            val step = reader.read(chars, read, contentLength - read)
                            if (step < 0) break
                            read += step
                        }
                    }
                    requests.add("${requestLine[1]} ${requestLine[0]}")
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
    fun `devices response exposes current device id`() {
        val payload = """
            {"devices":[{"device_id":"dev-1","name":"POCO","revoked_at":null},
            {"device_id":"dev-2","name":"Old","revoked_at":1700000000}],"current_device_id":"dev-1"}
        """.trimIndent()
        serve(listOf(payload)) { client, requests ->
            val response = client.devices()

            assertEquals(2, response.devices.size)
            assertEquals("dev-1", response.currentDeviceId)
            assertEquals("dev-2", response.devices[1].deviceId)
            assertEquals(1700000000L, response.devices[1].revokedAt ?: -1L)
            assertEquals("/mobile/v1/devices GET", requests.single())
        }
    }

    @Test
    fun `purge revoked devices posts to bulk endpoint`() {
        serve(listOf("""{"deleted":3}""")) { client, requests ->
            val result = client.purgeRevokedDevices()

            assertEquals("3", result["deleted"].toString())
            assertEquals("/mobile/v1/devices/purge-revoked POST", requests.single())
        }
    }

    @Test
    fun `delete device asks server for hard removal`() {
        serve(listOf("""{"deleted":true,"device_id":"dev-2"}""")) { client, requests ->
            val result = client.deleteDevice("dev-2")

            assertEquals("true", result["deleted"].toString())
            assertEquals("/mobile/v1/devices/dev-2?purge=1 DELETE", requests.single())
        }
    }
}