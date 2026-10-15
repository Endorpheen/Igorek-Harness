package ru.igorek.remote.data

import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeErrorStateTest {
    @Test
    fun networkFailureIsClearedAfterReconnect() {
        val kind = bridgeErrorKind(IOException("offline"))

        assertEquals(BridgeErrorKind.CONNECTION, kind)
        assertTrue(shouldClearBridgeErrorAfterReconnect(kind))
    }

    @Test
    fun serverFailureRemainsVisibleAfterReconnect() {
        val kind = bridgeErrorKind(BridgeException(500, "server failed"))

        assertEquals(BridgeErrorKind.OPERATION, kind)
        assertFalse(shouldClearBridgeErrorAfterReconnect(kind))
    }
}
