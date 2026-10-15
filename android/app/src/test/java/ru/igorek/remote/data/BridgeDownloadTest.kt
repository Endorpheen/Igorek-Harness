package ru.igorek.remote.data

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.ServerSocket
import java.net.InetAddress
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class BridgeDownloadTest {
    private fun serve(body: ByteArray, status: Int = 200, block: (BridgeClient, MutableList<String>) -> Unit) {
        val server = BridgeTlsFixture.server(1)
        val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
        val worker = Thread {
            server.accept().use { socket ->
                socket.soTimeout = 5000
                val reader = socket.getInputStream().bufferedReader()
                val path = reader.readLine().split(" ")[1]
                var authorization = ""
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    if (line.startsWith("Authorization:", ignoreCase = true)) authorization = line.substringAfter(':').trim()
                }
                requests.add("$path|$authorization")
                socket.getOutputStream().use { output ->
                    output.write("HTTP/1.1 $status OK\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray())
                    output.write(body)
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
    fun `download preserves bytes and authenticates request`() {
        val bytes = ByteArray(150000) { (it % 251).toByte() }
        serve(bytes) { client, requests ->
            val output = ByteArrayOutputStream()
            var progress = 0L
            client.download("session-1", AttachmentDto("file-id", "report.xlsx", bytes.size.toLong()), output) { progress = it }
            assertArrayEquals(bytes, output.toByteArray())
            assertEquals(bytes.size.toLong(), progress)
            assertEquals("/mobile/v1/sessions/session-1/files/file-id|Bearer test-token", requests.single())
        }
    }

    @Test
    fun `changed size does not save partial file`() {
        serve("short".toByteArray()) { client, _ ->
            val output = ByteArrayOutputStream()
            assertThrows(IOException::class.java) {
                client.download("session-1", AttachmentDto("file-id", "report.txt", 100), output) {}
            }
            assertEquals(0, output.size())
        }
    }

    @Test
    fun `expired file identifier returns recoverable error`() {
        serve("missing".toByteArray(), 404) { client, _ ->
            val failure = assertThrows(BridgeException::class.java) {
                client.download("session-1", AttachmentDto("old-id", "report.txt", 7), ByteArrayOutputStream()) {}
            }
            assertEquals(404, failure.status)
        }
    }

    @Test
    fun `attachment list decodes unicode name and size`() {
        val response = """{"files":[{"id":"abc","name":"отчёт.pdf","size":100,"mime_type":"application/pdf"}]}"""
        serve(response.toByteArray()) { client, _ ->
            assertEquals(AttachmentDto("abc", "отчёт.pdf", 100, "application/pdf"), client.attachments("session-1").single())
        }
    }

    @Test
    fun `large attachments stream beyond old limit without retaining bytes`() {
        val size = 101L * 1024 * 1024
        val server = BridgeTlsFixture.server(1)
        val chunk = ByteArray(65536) { 42 }
        val worker = Thread {
            server.accept().use { socket ->
                val reader = socket.getInputStream().bufferedReader()
                while (!reader.readLine().isNullOrEmpty()) { }
                val output = socket.getOutputStream()
                output.write("HTTP/1.1 200 OK\r\nContent-Length: $size\r\nConnection: close\r\n\r\n".toByteArray())
                repeat((size / chunk.size).toInt()) { output.write(chunk) }
                output.flush()
            }
        }.apply { isDaemon = true; start() }
        var count = 0L
        val sink = object : java.io.OutputStream() {
            override fun write(value: Int) { count++ }
            override fun write(bytes: ByteArray, offset: Int, length: Int) { count += length }
        }
        try {
            var progress = 0L
            BridgeTlsFixture.client(server, "test-token").download("session", AttachmentDto("large", "song.wav", size), sink) { progress = it }
            assertEquals(size, count)
            assertEquals(size, progress)
        } finally {
            server.close()
            worker.join(5000)
        }
    }

    @Test
    fun `attachment larger than 500 mib is rejected before writing`() {
        serve(byteArrayOf()) { client, _ ->
            val output = ByteArrayOutputStream()
            val failure = assertThrows(IOException::class.java) {
                client.download("session", AttachmentDto("large", "song.wav", 524288001L), output) { }
            }
            org.junit.Assert.assertTrue(failure.message.orEmpty().contains("500"))
            assertEquals(0, output.size())
        }
    }
}
