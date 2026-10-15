package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test

class PairingQrTest {
    private val valid = """{"type":"igorek.desktop.pair","version":1,"url":"https://192.168.0.2:47822","code":"1234-ABCD-5678","fingerprint":"${"a".repeat(64)}","expires_at":1300,"name":"Десктоп"}"""
    @Test fun parsesPinnedConnectionWithoutExposingCodeInLogs() {
        val value=decodePairingQr(valid, 1000)!!
        assertEquals("https://192.168.0.2:47822", value.url)
        assertEquals("A".repeat(64), value.fingerprint)
        assertFalse(value.toString().contains(value.code))
    }
    @Test fun rejectsExpiredAndUnknownPayloads() {
        assertNull(decodePairingQr(valid, 2000))
        assertNull(decodePairingQr(valid.replace("igorek.desktop.pair", "other"), 1000))
        assertNull(decodePairingQr(valid.replace("\"version\":1", "\"version\":2"), 1000))
        assertNull(decodePairingQr("x".repeat(4097), 1000))
    }
    @Test fun rejectsHttpCredentialsQueriesAndInvalidPins() {
        for (bad in listOf(
            valid.replace("https://", "http://"),
            valid.replace("192.168.0.2", "user:pass@192.168.0.2"),
            valid.replace(":47822\"", ":47822/?token=secret\""),
            valid.replace("a".repeat(64), "invalid"),
        )) assertNull(decodePairingQr(bad, 1000))
    }
}
