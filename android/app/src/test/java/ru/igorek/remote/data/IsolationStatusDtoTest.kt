package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class IsolationStatusDtoTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `session detail without isolation decodes to null`() {
        val detail = json.decodeFromString(
            SessionDetail.serializer(),
            """{"session_id":"chat-1","messages":[],"tasks":[]}""",
        )

        println("old payload without isolation -> isolation=${detail.isolation}")
        assertNull(detail.isolation)
        assertEquals("chat-1", detail.sessionId)
    }

    @Test
    fun `session detail with kvm isolation decodes the value`() {
        val detail = json.decodeFromString(
            SessionDetail.serializer(),
            """{"session_id":"chat-1","isolation":"kvm"}""",
        )

        println("payload with kvm isolation -> isolation=${detail.isolation}")
        assertEquals("kvm", detail.isolation)
    }

    @Test
    fun `session summary without isolation decodes to null`() {
        val summary = json.decodeFromString(
            SessionSummary.serializer(),
            """{"session_id":"chat-1"}""",
        )

        assertNull(summary.isolation)
    }

    @Test
    fun `session summary keeps none and kvm values distinct`() {
        val off = json.decodeFromString(
            SessionSummary.serializer(),
            """{"session_id":"chat-1","isolation":"none"}""",
        )
        val on = json.decodeFromString(
            SessionSummary.serializer(),
            """{"session_id":"chat-1","isolation":"kvm"}""",
        )

        assertEquals("none", off.isolation)
        assertEquals("kvm", on.isolation)
    }
}
