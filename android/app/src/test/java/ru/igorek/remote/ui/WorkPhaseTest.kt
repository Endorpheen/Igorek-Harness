package ru.igorek.remote.ui

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Test

class WorkPhaseTest {
    @Test
    fun `waiting approval comes from status or pending approvals`() {
        assertEquals(WorkPhase.WaitingApproval, workPhase("waiting_approval", 0, false, false))
        assertEquals(WorkPhase.WaitingApproval, workPhase("  Waiting_Approval ", 0, false, false))
        assertEquals(WorkPhase.WaitingApproval, workPhase("processing", 1, false, true))
        assertEquals(WorkPhase.WaitingApproval, workPhase("", 2, true, true))
    }

    @Test
    fun `waiting approval wins over cancelling and streaming`() {
        assertEquals(WorkPhase.WaitingApproval, workPhase("cancelling", 1, true, true))
        assertEquals(WorkPhase.WaitingApproval, workPhase("processing", 1, false, false))
    }

    @Test
    fun `cancelling maps to stopping`() {
        assertEquals(WorkPhase.Stopping, workPhase("cancelling", 0, false, false))
        assertEquals(WorkPhase.Stopping, workPhase(" CANCELLING ", 0, false, true))
        assertEquals(WorkPhase.Stopping, workPhase("cancelling", 0, true, true))
    }

    @Test
    fun `active statuses streaming and running task map to working`() {
        for (status in listOf("queued", "processing", "retry", "Processing", " RETRY ")) {
            assertEquals(status, WorkPhase.Working, workPhase(status, 0, false, false))
        }
        assertEquals(WorkPhase.Working, workPhase("completed", 0, true, false))
        assertEquals(WorkPhase.Working, workPhase("", 0, false, true))
        assertEquals(WorkPhase.Working, workPhase("   ", 0, false, true))
    }

    @Test
    fun `blank status without running task and terminal statuses are idle`() {
        assertEquals(WorkPhase.Idle, workPhase("", 0, false, false))
        assertEquals(WorkPhase.Idle, workPhase("   ", 0, false, false))
        assertEquals(WorkPhase.Idle, workPhase("completed", 0, false, true))
        assertEquals(WorkPhase.Idle, workPhase("failed", 0, false, false))
    }

    @Test
    fun `phase colours follow the palette`() {
        assertEquals(Color(0xFF44D7A8), workPhaseColor(WorkPhase.Idle))
        assertEquals(Color(0xFF44D7A8), workPhaseColor(WorkPhase.Working))
        assertEquals(Color(0xFFF6B94A), workPhaseColor(WorkPhase.Stopping))
        assertEquals(Color(0xFFF6B94A), workPhaseColor(WorkPhase.WaitingApproval))
    }

    @Test
    fun `connection colour keeps the online semantics`() {
        assertEquals(Color(0xFF44D7A8), connectionDotColor("online"))
        assertEquals(Color(0xFFF6B94A), connectionDotColor("offline"))
        assertEquals(Color(0xFFF6B94A), connectionDotColor("connecting"))
    }
}
