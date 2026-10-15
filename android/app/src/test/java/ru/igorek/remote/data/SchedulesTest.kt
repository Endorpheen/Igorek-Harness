package ru.igorek.remote.data

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicBoolean

class SchedulesTest {
    private data class Request(val method: String, val path: String, val auth: String, val body: String)
    private val payload = """{"available":true,"running":true,"schedules":[{"schedule_id":"job-1","name":"Daily","payload":"Task","schedule_type":"daily","time_value":"09:30","cron_expression":null,"timezone":"Europe/Moscow","enabled":true,"next_run_at":1800000000,"last_run_at":null,"run_count":0,"profile":"demo","cwd":"/tmp","last_session_id":null,"last_status":"","ready":true}]}"""
    private fun serve(responses: List<Pair<Int, String>>, block: (BridgeClient, MutableList<Request>) -> Unit) {
        val server = BridgeTlsFixture.server(4)
        val requests = java.util.Collections.synchronizedList(mutableListOf<Request>())
        val worker = Thread {
            try {
                for ((status, body) in responses) server.accept().use { socket ->
                    socket.soTimeout = 5000
                    val input = socket.getInputStream().buffered()
                    fun line(): String {
                        val out = java.io.ByteArrayOutputStream()
                        while (true) { val byte = input.read(); if (byte < 0 || byte == 10) break; if (byte != 13) out.write(byte) }
                        return out.toString("UTF-8")
                    }
                    val start = line().split(' ')
                    var length = 0; var auth = ""
                    while (true) {
                        val header = line()
                        if (header.isEmpty()) break
                        if (header.startsWith("Content-Length:", true)) length = header.substringAfter(':').trim().toInt()
                        if (header.startsWith("Authorization:", true)) auth = header.substringAfter(':').trim()
                    }
                    val bytesIn = ByteArray(length); var read = 0
                    while (read < length) { val n = input.read(bytesIn, read, length - read); if (n < 0) break; read += n }
                    requests.add(Request(start[0], start[1], auth, bytesIn.toString(Charsets.UTF_8)))
                    val bytes = body.toByteArray()
                    socket.getOutputStream().write("HTTP/1.1 $status Response\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray() + bytes)
                }
            } catch (_: java.net.SocketException) { }
        }.apply { isDaemon = true; start() }
        try { block(BridgeTlsFixture.client(server, "schedule-fixture-token"), requests) }
        finally { server.close(); worker.join(5000) }
    }
    private val fields = ScheduleFields("Проверка", "Скажи \"привет\"\nИ остановись.", "demo", "/tmp/project", "daily", "09:30", "Europe/Moscow")

    @Test fun listingDecodesNullFieldsAndUsesAuthenticatedEndpoint() {
        serve(listOf(200 to payload)) { client, requests ->
            val response = client.schedules()
            assertTrue(response.available && response.running)
            assertEquals("09:30", response.schedules.single().timeValue)
            assertNull(response.schedules.single().cronExpression)
            assertNull(response.schedules.single().lastSessionId)
            assertEquals(Request("GET", "/mobile/v1/schedules", "Bearer schedule-fixture-token", ""), requests.single())
        }
    }

    @Test fun createAndEditUseTheExistingExactSevenFieldContract() {
        serve(listOf(200 to payload, 200 to payload)) { client, requests ->
            client.saveSchedule(fields)
            client.saveSchedule(fields.copy(scheduleType = "once"), "job/1")
            assertEquals("POST", requests[0].method)
            assertEquals("/mobile/v1/schedules", requests[0].path)
            assertEquals("PATCH", requests[1].method)
            assertEquals("/mobile/v1/schedules/job%2F1", requests[1].path)
            val json = Json.parseToJsonElement(requests[0].body).jsonObject
            assertEquals(setOf("name", "payload", "profile", "cwd", "schedule_type", "time_value", "timezone"), json.keys)
            assertEquals(fields.payload, json["payload"]!!.jsonPrimitive.content)
            assertEquals("once", Json.parseToJsonElement(requests[1].body).jsonObject["schedule_type"]!!.jsonPrimitive.content)
            assertTrue(requests.all { it.auth == "Bearer schedule-fixture-token" })
        }
    }

    @Test fun pauseRunAndDeleteDoNotChangeOtherScheduleFields() {
        serve(List(3) { 200 to payload }) { client, requests ->
            client.setScheduleEnabled("job-1", false)
            client.runSchedule("job-1")
            client.deleteSchedule("job-1")
            assertEquals("PATCH", requests[0].method)
            assertEquals(buildJsonObject { put("enabled", false) }, Json.parseToJsonElement(requests[0].body))
            assertEquals(Request("POST", "/mobile/v1/schedules/job-1/run", "Bearer schedule-fixture-token", "{}"), requests[1])
            assertEquals("DELETE", requests[2].method)
            assertEquals("/mobile/v1/schedules/job-1", requests[2].path)
            assertEquals("", requests[2].body)
        }
    }

    @Test fun unavailableServerAndValidationFailureRemainExplicit() {
        serve(listOf(200 to """{"available":false,"running":false,"schedules":[]}""", 400 to """{"error":"Unknown time zone"}""")) { client, _ ->
            assertFalse(client.schedules().available)
            val error = runCatching { client.saveSchedule(fields) }.exceptionOrNull() as BridgeException
            assertEquals(400, error.status)
            assertEquals("Unknown time zone", error.message)
        }
    }

    @Test fun aConnectionReplacedBeforeMutationNeverSendsTheMutation() = runBlocking {
        val error = runCatching { SchedulesConnection(BridgeClient("https://127.0.0.1:1", "fixture")) { false }.run("job-1") }.exceptionOrNull()
        assertEquals("connection_changed", (error as BridgeException).code)
    }

    @Test fun aDelayedResponseFromThePreviousConnectionIsRejected() {
        val current = AtomicBoolean(true)
        serve(listOf(200 to payload)) { client, _ ->
            runBlocking {
                val bound = SchedulesConnection(client) { current.getAndSet(false) }
                val error = runCatching { bound.list() }.exceptionOrNull()
                assertEquals("connection_changed", (error as BridgeException).code)
            }
        }
    }

    @Test fun activeRunHasPriorityOverPausingFutureRuns() {
        assertEquals(ScheduleStatus.Running, scheduleStatus(ScheduledJobDto(enabled = false, lastStatus = "processing")))
        assertEquals(ScheduleStatus.Approval, scheduleStatus(ScheduledJobDto(lastStatus = "waiting_approval")))
        assertEquals(ScheduleStatus.Paused, scheduleStatus(ScheduledJobDto(enabled = false)))
    }

    @Test fun aFinishedOnceJobIsNotMistakenForAnotherPendingRun() {
        val done = ScheduledJobDto(enabled = true, scheduleType = "once", runCount = 1, ready = true)
        assertEquals(ScheduleStatus.Done, scheduleStatus(done))
        assertEquals(ScheduleStatus.Failed, scheduleStatus(done.copy(lastStatus = "fail")))
        assertEquals(ScheduleStatus.Waiting, scheduleStatus(done.copy(scheduleType = "daily", ready = false)))
        assertEquals(ScheduleStatus.Scheduled, scheduleStatus(done.copy(scheduleType = "daily", nextRunAt = 1800000000)))
    }

    @Test fun timestampsUseTheScheduleTimeZoneAndHandleMissingValues() {
        val time = 1800000000L
        assertTrue(scheduleTimestamp(time, "Europe/Moscow", AppLanguage.English).endsWith("11:00"))
        assertTrue(scheduleTimestamp(time, "UTC", AppLanguage.English).endsWith("08:00"))
        assertEquals("—", scheduleTimestamp(null, "UTC", AppLanguage.Russian))
        assertEquals("—", scheduleTimestamp(time, "not-a-zone", AppLanguage.Russian))
    }

    @Test fun validationPreservesCronAndStoppedProfilesByNotSavingThemAsDaily() {
        assertTrue(scheduleFieldsValid(fields, listOf("demo")))
        assertFalse(scheduleFieldsValid(fields.copy(scheduleType = "cron"), listOf("demo")))
        assertFalse(scheduleFieldsValid(fields.copy(timeValue = "24:00"), listOf("demo")))
        assertFalse(scheduleFieldsValid(fields, emptyList()))
        assertFalse(scheduleFieldsValid(fields.copy(payload = "  "), listOf("demo")))
    }
}
