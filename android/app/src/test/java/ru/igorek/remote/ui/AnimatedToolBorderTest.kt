package ru.igorek.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AnimatedToolBorderTest {

    @Test
    fun `span before the seam moves to the end of the perimeter`() {
        // The tail extends into negative distances: it continues at the perimeter’s end.
        assertEquals(listOf(70f to 100f), normalizePerimeterSpan(-30f, 0f, 100f))
    }

    @Test
    fun `span crossing the seam splits into two parts`() {
        assertEquals(
            listOf(90f to 100f, 0f to 10f),
            normalizePerimeterSpan(90f, 110f, 100f),
        )
    }

    @Test
    fun `span longer than perimeter covers it fully`() {
        assertEquals(listOf(0f to 100f), normalizePerimeterSpan(10f, 500f, 100f))
    }

    @Test
    fun `span is empty for degenerate perimeter or reversed bounds`() {
        assertTrue(normalizePerimeterSpan(0f, 10f, 0f).isEmpty())
        assertTrue(normalizePerimeterSpan(50f, 50f, 100f).isEmpty())
        assertTrue(normalizePerimeterSpan(60f, 40f, 100f).isEmpty())
    }

    @Test
    fun `trace is empty on degenerate perimeter`() {
        assertTrue(toolBorderTrace(0.5f, 0f).isEmpty())
        assertTrue(toolBorderTrace(0.5f, -10f).isEmpty())
        assertTrue(toolBorderTrace(0.5f, 100f, steps = 0).isEmpty())
    }

    @Test
    fun `trace keeps the requested tail and head lengths`() {
        val trace = toolBorderTrace(0.5f, 1000f, tailFraction = 0.30f, headFraction = 0.12f, steps = 5)

        val head = trace.filter { it.alpha >= 1f }
        val tail = trace.filter { it.alpha < 1f }

        assertEquals(120f, head.sumOf { (it.end - it.start).toDouble() }.toFloat(), 0.01f)
        assertEquals(300f, tail.sumOf { (it.end - it.start).toDouble() }.toFloat(), 0.01f)
        assertEquals(ToolBorderTailPeakAlpha, tail.maxOf { it.alpha }, 0.0001f)
    }

    @Test
    fun `trace brightness grows from the tail tip to the head`() {
        val trace = toolBorderTrace(0.5f, 1000f, steps = 4)
        val tailAlphas = trace.filter { it.alpha < 1f }.map { it.alpha }

        assertEquals(tailAlphas.sorted(), tailAlphas)
        assertTrue(tailAlphas.first() < tailAlphas.last())
        assertTrue(trace.any { it.alpha == 1f })
    }

    @Test
    fun `trace segments always stay inside the perimeter`() {
        val total = 640f
        for (step in 0..20) {
            val trace = toolBorderTrace(step / 20f, total)
            assertTrue(trace.isNotEmpty())
            trace.forEach { segment ->
                assertTrue(segment.start >= 0f)
                assertTrue(segment.end <= total + 0.01f)
                assertTrue(segment.end > segment.start)
            }
        }
    }

    @Test
    fun `trace normalizes phase modulo full turn`() {
        val base = toolBorderTrace(0.25f, 500f)
        assertEquals(base, toolBorderTrace(1.25f, 500f))
        assertEquals(base, toolBorderTrace(-0.75f, 500f))
    }

    @Test
    fun `pending approval keeps the animated border until the user decides`() {
        assertTrue(isApprovalTraceActive(resolved = null, locallyDecided = false))
        assertTrue(isApprovalTraceActive(resolved = "", locallyDecided = false))
        assertTrue(isApprovalTraceActive(resolved = "  ", locallyDecided = false))
    }

    @Test
    fun `approval border stops after a decision is recorded`() {
        assertFalse(isApprovalTraceActive(resolved = null, locallyDecided = true))
        assertFalse(isApprovalTraceActive(resolved = "allow_once", locallyDecided = false))
        assertFalse(isApprovalTraceActive(resolved = "reject", locallyDecided = true))
        assertFalse(isApprovalTraceActive(resolved = "allow_session", locallyDecided = false))
    }

    @Test
    fun `only running tool status gets the animated border`() {
        assertTrue(isRunningToolStatus("running"))
        assertTrue(isRunningToolStatus("  Running "))
        assertTrue(isRunningToolStatus("RUNNING"))
        assertFalse(isRunningToolStatus("done"))
        assertFalse(isRunningToolStatus("error"))
        assertFalse(isRunningToolStatus("deferred"))
        assertFalse(isRunningToolStatus(""))
    }
}
