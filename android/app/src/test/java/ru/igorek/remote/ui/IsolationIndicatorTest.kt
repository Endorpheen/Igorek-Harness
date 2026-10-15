package ru.igorek.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class IsolationIndicatorTest {
    @Test
    fun `kvm isolation yields the badge label`() {
        assertEquals("KVM", isolationIndicatorLabel("kvm"))
        assertEquals("KVM", isolationIndicatorLabel("KVM"))
        assertEquals("KVM", isolationIndicatorLabel(" kvm "))
    }

    @Test
    fun `absent none and unknown values render no indicator`() {
        assertNull(isolationIndicatorLabel(null))
        assertNull(isolationIndicatorLabel(""))
        assertNull(isolationIndicatorLabel("none"))
        assertNull(isolationIndicatorLabel("bwrap"))
    }
}
