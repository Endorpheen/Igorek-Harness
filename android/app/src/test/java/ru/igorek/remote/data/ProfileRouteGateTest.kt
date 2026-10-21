package ru.igorek.remote.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProfileRouteGateTest {
    @Test fun `switch cannot race a send from any chat`() {
        val gate = ProfileRouteGate()
        assertTrue(gate.beginSend())
        assertTrue(gate.beginSend())
        assertFalse(gate.beginSwitch())
        gate.endSend()
        assertFalse(gate.beginSwitch())
        gate.endSend()
        assertTrue(gate.beginSwitch())
        assertFalse(gate.beginSend())
        assertFalse(gate.beginSwitch())
        gate.endSwitch()
        assertTrue(gate.beginSend())
        gate.endSend()
        assertTrue(gate.beginSwitch())
    }

    @Test fun `releasing a failed switch permits retry and sending`() {
        val gate = ProfileRouteGate()
        assertTrue(gate.beginSwitch())
        gate.endSwitch()
        assertTrue(gate.beginSwitch())
        gate.endSwitch()
        assertTrue(gate.beginSend())
        gate.endSend()
    }
}
