package ru.igorek.remote.ui

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.igorek.remote.data.SessionSummary
import ru.igorek.remote.data.SessionsResponse
import ru.igorek.remote.data.UpdateSessionRequest

class SessionPinsTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }
    private fun chat(id: String, time: Long, pinned: Boolean = false) =
        SessionSummary(sessionId = id, updatedAt = time, pinned = pinned)

    @Test fun `old servers default to unpinned and new servers retain the flag`() {
        val response = json.decodeFromString<SessionsResponse>("""{"sessions":[{"session_id":"old"},{"session_id":"pin","pinned":true},{"session_id":"ordinary","pinned":false}]}""")
        assertEquals(listOf(false, true, false), response.sessions.map { it.pinned })
    }

    @Test fun `three older pins remain ahead of newer ordinary chats`() {
        val rows = listOf(chat("new", 100), chat("p1", 1, true), chat("p3", 3, true), chat("p2", 2, true), chat("other", 90))
        assertEquals(listOf("p3", "p2", "p1", "new", "other"), orderedSessions(rows).map { it.sessionId })
        assertEquals("new", rows.first().sessionId)
    }

    @Test fun `more than three pins are retained and equal timestamps preserve order`() {
        val rows = listOf(chat("ordinary", 99)) + (1..4).map { chat("pin$it", 1, true) }
        assertEquals(listOf("pin1", "pin2", "pin3", "pin4", "ordinary"), orderedSessions(rows).map { it.sessionId })
        assertTrue(orderedSessions(emptyList()).isEmpty())
    }

    @Test fun `unpin restores chronological order without losing session state`() {
        val original = chat("old", 1, true).copy(active = true, archived = true, title = "Retained", pendingApprovals = 2)
        val result = orderedSessions(listOf(original.copy(pinned = false), chat("new", 10)))
        assertEquals(listOf("new", "old"), result.map { it.sessionId })
        val old = result.last()
        assertFalse(old.pinned)
        assertTrue(old.active && old.archived)
        assertEquals(2, old.pendingApprovals)
        assertEquals("Retained", old.title)
    }

    @Test fun `pin patch sends explicit booleans while unrelated writes omit the field`() {
        assertEquals("""{"pinned":true}""", json.encodeToString(UpdateSessionRequest.serializer(), UpdateSessionRequest(pinned = true)))
        assertEquals("""{"pinned":false}""", json.encodeToString(UpdateSessionRequest.serializer(), UpdateSessionRequest(pinned = false)))
        assertEquals("""{"title":"Renamed"}""", json.encodeToString(UpdateSessionRequest.serializer(), UpdateSessionRequest(title = "Renamed")))
        assertEquals("""{"archived":true}""", json.encodeToString(UpdateSessionRequest.serializer(), UpdateSessionRequest(archived = true)))
    }
}
