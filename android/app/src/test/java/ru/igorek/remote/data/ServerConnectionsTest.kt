package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test

class ServerConnectionsTest {
    @Test fun preservesIndependentCredentialsAndSequenceCounters() {
        val web = ServerConnection("web", "Web UI", "https://web.example", "web-token", "web-pin", lastSeq = 91, profile = "opencode")
        val desktop = ServerConnection("desktop", "Десктоп", "https://desktop.example", "desktop-token", "desktop-pin", lastSeq = 7, profile = "codexpro")
        val values = ConnectionCodec.decode(ConnectionCodec.encode(ConnectionCodec.upsert(listOf(web), desktop)))
        assertEquals(2, values.size)
        assertEquals("web-token", values[0].token)
        assertEquals(91L, values[0].lastSeq)
        assertEquals("codexpro", values[1].profile)
        assertEquals("desktop-pin", values[1].fingerprint)
    }
    @Test fun rePairingSameAddressReplacesOnlyThatConnection() {
        val web = ServerConnection("web", "Web UI", "https://web.example", "old")
        val desktop = ServerConnection("desktop", "Десктоп", "https://desktop.example", "other")
        val renewed = web.copy(id = "new", token = "renewed")
        val values = ConnectionCodec.upsert(listOf(web, desktop), renewed)
        assertEquals(2, values.size)
        assertEquals("other", values.first().token)
        assertEquals("renewed", values.last().token)
        assertTrue(ConnectionCodec.decode("invalid").isEmpty())
    }
    @Test fun displayMetadataContainsNoCredentials() {
        val display = SavedConnection("desktop", "Десктоп", "https://desktop.example", true)
        assertFalse(display.toString().contains("token"))
        assertFalse(display.toString().contains("fingerprint"))
    }
}
