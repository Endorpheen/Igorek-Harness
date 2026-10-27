package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionGoalCommandTest {
    private val goal = SessionGoal(goalId = "goal-1", objective = "Old", status = "active")

    @Test fun `ordinary messages are not commands`() {
        assertNull(parseSessionGoalCommand("please finish this goal", goal))
        assertNull(parseSessionGoalCommand("/goalkeeper", goal))
    }

    @Test fun `create and edit preserve explicit objective`() {
        assertEquals(SessionGoalCommand.Action("create", objective = "Build feature"), parseSessionGoalCommand("/goal Build feature", null))
        assertEquals(SessionGoalCommand.Action("create", objective = "Line one\nLine two"), parseSessionGoalCommand("/goal\nLine one\nLine two", null))
        assertEquals(SessionGoalCommand.Action("edit", goalId = "goal-1", objective = "New goal"), parseSessionGoalCommand("/goal edit New goal", goal))
    }

    @Test fun `controls use current goal identity`() {
        assertEquals(SessionGoalCommand.Show, parseSessionGoalCommand("/goal", goal))
        for (action in listOf("pause", "resume", "clear")) {
            assertEquals(SessionGoalCommand.Action(action, goalId = "goal-1"), parseSessionGoalCommand("/goal $action", goal))
            assertTrue(parseSessionGoalCommand("/goal $action", null) is SessionGoalCommand.Error)
        }
        assertTrue(parseSessionGoalCommand("/goal", null) is SessionGoalCommand.Error)
        assertTrue(parseSessionGoalCommand("/goal edit revised", null) is SessionGoalCommand.Error)
    }

    @Test fun `session detail decodes goal and remains compatible with older servers`() {
        val json = Json { ignoreUnknownKeys = true }
        val detail = json.decodeFromString<SessionDetail>("""{"session_id":"chat","goal":{"goal_id":"g1","objective":"Work","status":"active","tokens_used":12,"token_budget":50}}""")
        assertEquals("g1", detail.goal?.goalId)
        assertEquals(12L, detail.goal?.tokensUsed)
        assertEquals(50L, detail.goal?.tokenBudget)
        assertNull(json.decodeFromString<SessionDetail>("""{"session_id":"chat"}""").goal)
    }
}
