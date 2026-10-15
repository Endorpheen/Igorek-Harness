package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test

class VoiceRoutesTest {
    @Test fun permitsOnlyVoiceAndCurrentSessionOperations() {
        assertEquals("/voice/config", voiceRoute("chat-123", "/api/voice/config"))
        assertEquals("/sessions/chat-123/messages", voiceRoute("chat-123", "/api/sessions/chat-123/messages"))
        assertEquals("/tasks/task-1/approvals/request-1", voiceRoute("chat-123", "/api/tasks/task-1/approvals/request-1"))
        for (path in listOf("/api/sessions/other/messages", "https://evil/api/voice/sdp", "/api/voice/../files", "/api/voice/config?token=x")) {
            assertTrue(runCatching { voiceRoute("chat-123", path) }.isFailure)
        }
    }
}
