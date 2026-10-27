package ru.igorek.remote.data

import org.junit.After
import org.junit.Assert.*
import org.junit.Test

class SlashCommandsTest {
    private val originalLanguage = AppLocale.current
    private val goal = SessionGoal(goalId = "fixture", objective = "Work", status = "active")
    private fun commands(text: String, current: SessionGoal? = goal) = matchingSlashCommands(text, current).map { it.command }

    @After fun restoreLanguage() { AppLocale.set(originalLanguage) }

    @Test fun `ordinary text skill mentions paths and multiline drafts keep their original meaning`() {
        for (text in listOf("", "hello", "please /goal", "\$skill", "/unknown", "/goalkeeper", "/goal Build feature",
            "/goal edit New objective", "/goal\nWork", "/goal\rWork", "/tmp/project")) {
            assertTrue(text, commands(text).isEmpty())
        }
    }

    @Test fun `slash advertises only implemented commands for the current goal state`() {
        assertEquals(listOf("/help", "/status", "/goal"), commands("/", null))
        assertEquals(listOf("/help", "/status", "/goal", "/goal pause", "/goal edit", "/goal clear"), commands("/"))
        for (status in listOf("paused", "blocked", "usage_limited")) {
            assertEquals(listOf("/help", "/status", "/goal", "/goal resume", "/goal edit", "/goal clear"), commands("/", goal.copy(status = status)))
        }
        for (status in listOf("complete", "budget_limited", "unknown")) {
            assertEquals(listOf("/help", "/status", "/goal", "/goal edit", "/goal clear"), commands("/", goal.copy(status = status)))
        }
    }

    @Test fun `prefix filters progressively and completion stays compatible with the parser`() {
        assertEquals(listOf("/goal", "/goal pause", "/goal edit", "/goal clear"), commands(" /GO"))
        assertEquals(listOf("/help"), commands("/h"))
        assertEquals(listOf("/status"), commands("/st"))
        assertTrue(commands("/p").isEmpty())
        assertTrue(commands("/fast").isEmpty())
        assertTrue(commands("/ultrafast").isEmpty())
        assertEquals(listOf("/goal pause"), commands("/goal p"))
        assertEquals(listOf("/goal edit"), commands("/goal e"))
        assertEquals(listOf("/goal clear"), commands("/goal c"))
        assertEquals(listOf("/goal resume"), commands("/goal r", goal.copy(status = "paused")))
        for (suggestion in matchingSlashCommands("/go", goal)) {
            val parsed = parseSessionGoalCommand(suggestion.completion + if (suggestion.command.endsWith("edit")) "New objective" else "", goal)
            assertTrue(suggestion.command, parsed is SessionGoalCommand.Show || parsed is SessionGoalCommand.Action)
        }
        val create = matchingSlashCommands("/go", null).single()
        assertEquals("/goal ", create.completion)
        assertEquals(SessionGoalCommand.Action("create", objective = "Build feature"), parseSessionGoalCommand(create.completion + "Build feature", null))
    }

    @Test fun `selected controls hide suggestions and goal space offers available actions`() {
        assertEquals(listOf("/goal pause", "/goal edit", "/goal clear"), commands("/goal "))
        assertTrue(commands("/goal ", null).isEmpty())
        for (text in listOf("/goal pause ", "/goal clear ", "/goal edit ", "/goal resume ")) assertTrue(commands(text).isEmpty())
    }

    @Test fun `descriptions follow selected language and explain objective arguments`() {
        AppLocale.set(AppLanguage.Russian)
        assertTrue(matchingSlashCommands("/go", null).single().description.contains("текст цели"))
        assertTrue(matchingSlashCommands("/goal edit", goal).single().description.contains("новый текст"))
        AppLocale.set(AppLanguage.English)
        assertEquals("Set a goal: /goal objective", matchingSlashCommands("/go", null).single().description)
        assertEquals("Show the current goal", matchingSlashCommands("/go", goal).first().description)
        assertEquals("Resume goal execution", matchingSlashCommands("/goal r", goal.copy(status = "paused")).single().description)
    }
}
