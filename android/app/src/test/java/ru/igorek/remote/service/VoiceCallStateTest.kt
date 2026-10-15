package ru.igorek.remote.service

import org.junit.Assert.*
import org.junit.Test

class VoiceCallStateTest {
    @Test fun timerWaitsForRealTransport() {
        val connecting = VoiceCallState(sessionId = "chat-1", running = true)
        assertTrue(connecting.ringing)
        val premature = connecting.transport("listening", false, 100)
        assertEquals(0L, premature.connectedAt)
        val established = premature.transport("listening", true, 200)
        assertEquals(200L, established.connectedAt)
        assertFalse(established.ringing)
    }

    @Test fun minimizeAndReconnectKeepTheSameCallClock() {
        val call = VoiceCallState(sessionId = "chat-1", running = true).transport("listening", true, 200)
        val minimized = call.copy(expanded = false, micMuted = true)
        val reconnect = minimized.transport("reconnecting", false, 500)
        val restored = reconnect.copy(expanded = true).transport("listening", true, 900)
        assertEquals(200L, restored.connectedAt)
        assertEquals("chat-1", restored.sessionId)
        assertTrue(restored.micMuted)
        assertFalse(reconnect.ringing)
    }

    @Test fun stoppedCallCannotLeaveOverlayOrRing() {
        val call = VoiceCallState(sessionId = "chat-1", running = true)
        assertTrue(call.showOverlay)
        val stopped = call.copy(running = false, phase = "error")
        assertFalse(stopped.showOverlay)
        assertFalse(stopped.ringing)
        assertFalse(VoiceCallState().showOverlay)
    }
    @Test fun waitingToneStopsForFirstAnswerMuteApprovalOrConnectionLoss() {
        val waiting = VoiceCallState(sessionId = "chat-1", running = true, connected = true, waitingForAgent = true)
        assertTrue(waiting.holdTone)
        for (state in listOf(waiting.copy(waitingForAgent = false), waiting.copy(speakerMuted = true), waiting.copy(speaking = true), waiting.copy(pttRecording = true), waiting.copy(approval = true), waiting.copy(connected = false), waiting.copy(phase = "reconnecting"), waiting.copy(phase = "error"), waiting.copy(running = false))) assertFalse(state.holdTone)
    }

    @Test fun newCallDefaultsToClosedPushToTalk() {
        val state = VoiceCallState()
        assertEquals("ptt", state.micMode)
        assertTrue(state.micMuted)
        assertFalse(state.pttRecording)
    }

}
