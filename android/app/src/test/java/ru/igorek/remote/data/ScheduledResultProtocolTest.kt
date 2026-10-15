package ru.igorek.remote.data

import java.io.ByteArrayOutputStream
import org.junit.Assert.*
import org.junit.Test

class ScheduledResultProtocolTest {
    @Test fun `source session result survives HTTPS replay history and authenticated download`() {
        val source = "chat-enews"
        val fileId = "a".repeat(64)
        val bytes = "fixture image".toByteArray()
        val files = """[{"id":"$fileId","name":"post.jpg","size":${bytes.size},"mime_type":"image/jpeg","modified_at":123}]"""
        val bodies = listOf(
            """{"events":[{"kind":"schedule.delivered","seq":21,"session_id":"$source","task_id":"run-1","payload":{"message_id":"schedule-run-1","text":"News draft","files":$files}}]}""".toByteArray(),
            """{"session_id":"$source","messages":[{"role":"assistant","message_id":"schedule-run-1","text":"News draft","files":$files}]}""".toByteArray(),
            bytes,
        )
        val server = BridgeTlsFixture.server(3)
        val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
        val worker = Thread {
            bodies.forEach { body -> server.accept().use { socket ->
                socket.soTimeout = 5000
                val reader = socket.getInputStream().bufferedReader(); val path = reader.readLine().split(" ")[1]
                var auth = ""
                while(true) { val line = reader.readLine() ?: break; if(line.isEmpty())break
                    if(line.startsWith("Authorization:",ignoreCase=true))auth=line.substringAfter(':').trim()
                }
                requests += "$path|$auth"
                socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray()+body)
            } }
        }.apply { isDaemon=true; start() }
        try {
            val client = BridgeTlsFixture.client(server,"fixture-token")
            val timeline = SessionTimeline(source)
            client.sessionEvents(source).forEach(timeline::apply)
            timeline.seedFromHistory(client.session(source).messages)
            val row = timeline.snapshot().single() as TimelineItem.AssistantText
            assertEquals("News draft",row.text)
            val output=ByteArrayOutputStream();client.download(source,row.files.single(),output){}
            assertArrayEquals(bytes,output.toByteArray())
            assertEquals(listOf(
                "/mobile/v1/sessions/$source/events?after=0&limit=500|Bearer fixture-token",
                "/mobile/v1/sessions/$source|Bearer fixture-token",
                "/mobile/v1/sessions/$source/files/$fileId|Bearer fixture-token",
            ),requests)
        } finally { server.close();worker.join(5000) }
    }
}
