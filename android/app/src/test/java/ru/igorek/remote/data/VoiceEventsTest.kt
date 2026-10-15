package ru.igorek.remote.data

import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test

class VoiceEventsTest {
    @Test fun resolvedVoiceRequestIsRememberedBeforeTheChatTimelineIsOpened() {
        Bridge.markResolvedLocally("voice-cache-task", "request-a", "reject", "voice-cache-session")
        assertTrue(Bridge.isApprovalAlreadyResolved("voice-cache-task", "request-a"))
        assertFalse(Bridge.isApprovalAlreadyResolved("voice-cache-task", "request-b"))
        assertFalse(Bridge.isApprovalAlreadyResolved("different-task", "request-a"))
    }

    @Test fun voiceDecisionUsesTheExactApprovedRequestAndNotOtherVoicePosts() {
        assertEquals(VoiceApprovalDecision("task-1", "req-2", "reject"),
            voiceApprovalDecision("/api/tasks/task-1/approvals/req-2", """{"decision":"reject"}"""))
        assertNull(voiceApprovalDecision("/api/voice/session", """{"decision":"allow_once"}"""))
        assertNull(voiceApprovalDecision("/api/tasks/task-1/approvals/req-2", null))
        assertNull(voiceApprovalDecision("/api/tasks/task-1/approvals/req-2", """{"decision":"unknown"}"""))
        assertNull(voiceApprovalDecision("/api/tasks/task-1/approvals/req-2", "{"))
    }

    @Test fun approvalDecisionsReachVoiceDuringReconnectReplay() {
        for (kind in listOf("approval.required", "approval.resolved")) {
            assertTrue(shouldForwardVoiceEvent(BridgeEvent(kind = kind, seq = 7), replayThrough = 10))
        }
    }

    @Test fun replayedModelAnswersAreNotSpokenAgain() {
        assertFalse(shouldForwardVoiceEvent(BridgeEvent(kind = "model.round_response", seq = 7), 10))
        assertTrue(shouldForwardVoiceEvent(BridgeEvent(kind = "model.round_response", seq = 11), 10))
        assertFalse(shouldForwardVoiceEvent(BridgeEvent(kind = "tool.result", seq = 11), 10))
    }

    @Test fun chatDecisionImmediatelyReachesVoiceWithoutAnOpenTimeline() = runBlocking {
        val next = async(start = CoroutineStart.UNDISPATCHED) { withTimeout(1000) { Bridge.voiceEvents.first() } }
        Bridge.markResolvedLocally("voice-regression-task", "voice-regression-request", "allow_once", "voice-regression-session")
        val event = next.await()
        assertEquals("approval.resolved", event.kind)
        assertEquals("voice-regression-session", event.sessionId)
        assertEquals("voice-regression-task", event.taskId)
        assertEquals("voice-regression-request", event.payload["request_id"]?.toString()?.trim('"'))
        assertEquals("allow_once", event.payload["decision"]?.toString()?.trim('"'))
    }
}
