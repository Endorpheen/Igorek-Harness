package ru.igorek.remote.data

sealed interface SessionGoalCommand {
    data object Show : SessionGoalCommand
    data class Action(val action: String, val goalId: String? = null, val objective: String? = null) : SessionGoalCommand
    data class Error(val message: String) : SessionGoalCommand
}

fun parseSessionGoalCommand(text: String, current: SessionGoal?): SessionGoalCommand? {
    if (!Regex("^/goal(?:\\s|$)").containsMatchIn(text)) return null
    val value = text.removePrefix("/goal").trim()
    if (value.isEmpty()) return if (current != null) SessionGoalCommand.Show
        else SessionGoalCommand.Error(AppLocale.text("Напишите /goal и текст цели", "Use /goal followed by an objective"))
    if (value in setOf("pause", "resume", "clear")) {
        return if (current == null) SessionGoalCommand.Error(AppLocale.text("В этом чате нет цели", "No goal in this chat"))
        else SessionGoalCommand.Action(value, goalId = current.goalId)
    }
    if (value.startsWith("edit ")) {
        return if (current == null) SessionGoalCommand.Error(AppLocale.text("В этом чате нет цели", "No goal in this chat"))
        else SessionGoalCommand.Action("edit", goalId = current.goalId, objective = value.removePrefix("edit ").trim())
    }
    return SessionGoalCommand.Action("create", objective = value)
}
