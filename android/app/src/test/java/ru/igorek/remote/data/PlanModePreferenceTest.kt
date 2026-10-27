package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test

class PlanModePreferenceTest {
    @Test fun `keys are stable scoped and do not expose connection or session values`() {
        val key = planModePreferenceKey("connection", "https://fixture", "session")
        assertEquals(key, planModePreferenceKey("connection", "https://fixture", "session"))
        assertNotEquals(key, planModePreferenceKey("other", "https://fixture", "session"))
        assertNotEquals(key, planModePreferenceKey("connection", "https://other", "session"))
        assertNotEquals(key, planModePreferenceKey("connection", "https://fixture", "other"))
        assertFalse(key.contains("fixture"))
        assertFalse(key.contains("session"))
        assertNotEquals(planModePreferenceKey("a", "bc", "d"), planModePreferenceKey("ab", "c", "d"))
    }
}
