package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackgroundEventTimeoutsTest {
    private val timeout = ErrorObservation(code = "NET.READ_TIMEOUT", component = "mobile-events",
        operation = "read", endpointRole = "mobile_to_bridge", phase = "sse_read", owner = "bridge-events:fixture")

    @Test fun `background and short resume read timeouts can remain quiet`() {
        assertTrue(shouldQuietBackgroundTimeout(timeout, false, Long.MAX_VALUE))
        assertTrue(shouldQuietBackgroundTimeout(timeout, true, 6500))
        assertFalse(shouldQuietBackgroundTimeout(timeout, true, 15000))
        assertFalse(shouldQuietBackgroundTimeout(timeout, true, Long.MAX_VALUE))
    }

    @Test fun `real errors and uncertain writes are never hidden by this policy`() {
        val errors = listOf(timeout.copy(code = "AUTH.REJECTED"), timeout.copy(code = "TLS.CERTIFICATE_REJECTED"),
            timeout.copy(code = "HTTP.SERVICE_UNAVAILABLE"), timeout.copy(component = "model"),
            timeout.copy(component = "mobile-http"), timeout.copy(phase = "connect"),
            timeout.copy(operation = "send"), timeout.copy(taskId = "task"), timeout.copy(sessionId = "chat"),
            timeout.copy(clientOperationId = "message"), timeout.copy(httpStatus = 504),
            timeout.copy(outcome = "unknown"), timeout.copy(owner = "bridge-http:fixture"))
        for (error in errors) {
            assertFalse(error.toString(), shouldQuietBackgroundTimeout(error, false, 0))
            val entry = NotificationEntry("Real error", diagnostic = error, quiet = true)
            assertEquals(listOf(entry), visibleNotifications(listOf(entry)))
        }
    }

    @Test fun `quick recovery keeps the incident for diagnostics but out of the bell`() {
        val entries = mergeNotification(emptyList(), "Background timeout", "CONNECTION", 1000, timeout, quiet = true)
        val recovered = recoverNotifications(entries, timeout.owner, ErrorCatalog.stableAfterMs, 7500)
        assertEquals(1, recovered.size)
        assertEquals("recovered", recovered.single().state)
        assertTrue(visibleNotifications(recovered).isEmpty())
        assertEquals(0, unreadNotificationCount(recovered))
        assertEquals(recovered, promoteBackgroundTimeouts(recovered, timeout.owner))
    }

    @Test fun `persistent foreground outage is promoted without losing its diagnostic`() {
        val entries = mergeNotification(emptyList(), "Timeout", "CONNECTION", 1000, timeout, quiet = true)
        val visible = promoteBackgroundTimeouts(entries, timeout.owner)
        assertEquals(1, visibleNotifications(visible).size)
        assertEquals(1, unreadNotificationCount(visible))
        assertEquals(entries.single().id, visible.single().id)
        assertEquals(timeout.eventId, visible.single().diagnostic?.eventId)
        assertEquals(1, visible.single().count)
        assertEquals(entries, promoteBackgroundTimeouts(entries, "bridge-events:other"))
    }

    @Test fun `quiet reconnect does not downgrade an already visible outage`() {
        val first = mergeNotification(emptyList(), "Visible timeout", "CONNECTION", 1000, timeout)
        val merged = mergeNotification(first, "Again", "CONNECTION", 2000, timeout.copy(eventId = "second"), quiet = true)
        assertFalse(merged.single().quiet)
        assertEquals(1, visibleNotifications(merged).size)
    }

    @Test fun `regular error recording promotes a previously quiet incident`() {
        val quiet = mergeNotification(emptyList(), "Timeout", "CONNECTION", 1000, timeout, quiet = true)
        val visible = mergeNotification(quiet, "Still offline", "CONNECTION", 16000, timeout.copy(eventId = "next"))
        assertFalse(visible.single().quiet)
        assertEquals(2, visible.single().count)
    }

    @Test fun `quiet state survives persistence and legacy entries stay visible`() {
        val codec = Json { encodeDefaults = true }
        val entry = NotificationEntry("Expected timeout", diagnostic = timeout, quiet = true)
        assertTrue(codec.decodeFromString<NotificationEntry>(codec.encodeToString(NotificationEntry.serializer(), entry)).quiet)
        val legacy = codec.decodeFromString<NotificationEntry>("""{"text":"Old error"}""")
        assertFalse(legacy.quiet)
        assertEquals(listOf(legacy), visibleNotifications(listOf(legacy)))
    }

    @Test fun `quiet reconnects cannot evict existing real error cards`() {
        val real = (1..NOTIFICATION_LIMIT).map { NotificationEntry("Real error $it", diagnostic = timeout.copy(code = "AUTH.REJECTED")) }
        val updated = mergeNotification(real, "Expected timeout", "CONNECTION", 1, timeout, quiet = true)
        assertEquals(real.map { it.id }, visibleNotifications(updated).map { it.id })
        assertEquals(NOTIFICATION_LIMIT, unreadNotificationCount(updated))
    }
}
