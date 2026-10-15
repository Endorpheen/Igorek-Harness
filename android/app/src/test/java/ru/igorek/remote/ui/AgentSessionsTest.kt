package ru.igorek.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Test
import ru.igorek.remote.data.AgentSessionDto

class AgentSessionsTest {
    @Test fun nestedAgentsRemainNavigableRegardlessOfArrivalOrder() {
        val nodes = listOf(
            AgentSessionDto("web", "research", title = "Web Researcher", status = "running"),
            AgentSessionDto("main", title = "Main"),
            AgentSessionDto("coder", "main", status = "waiting_approval"),
            AgentSessionDto("research", "main", status = "running"),
        )
        val ordered = orderedAgentSessions(nodes)
        assertEquals(listOf("main", "coder", "research", "web"), ordered.map { it.first.sessionId })
        assertEquals(listOf(0, 1, 1, 2), ordered.map { it.second })
    }

    @Test fun missingParentsAndCyclesStillExposeEveryAgentOnce() {
        val nodes = listOf(AgentSessionDto("orphan", "missing"), AgentSessionDto("a", "b"), AgentSessionDto("b", "a"))
        assertEquals(setOf("orphan", "a", "b"), orderedAgentSessions(nodes).map { it.first.sessionId }.toSet())
        assertEquals(3, orderedAgentSessions(nodes).size)
    }
}
