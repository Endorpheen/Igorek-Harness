package ru.igorek.remote.data

data class SlashCommandSuggestion(val command: String, val description: String) {
    val completion: String get() = "$command "
}

fun matchingSlashCommands(draft: String, goal: SessionGoal?): List<SlashCommandSuggestion> {
    val query = draft.trimStart()
    if (!query.startsWith("/") || query.contains('\n') || query.contains('\r')) return emptyList()
    if (query.endsWith(" ") && query.trimEnd().contains(' ')) return emptyList()
    val commands = buildList {
        add(SlashCommandSuggestion("/help", AppLocale.text("Справка по командам", "Show command help")))
        add(SlashCommandSuggestion("/status", AppLocale.text("Состояние чата и контекста", "Show chat and context status")))
        add(SlashCommandSuggestion("/goal", if (goal == null)
            AppLocale.text("Задать цель: /goal текст цели", "Set a goal: /goal objective")
            else AppLocale.text("Показать текущую цель", "Show the current goal")))
        if (goal != null) {
            if (goal.status == "active") add(SlashCommandSuggestion("/goal pause",
                AppLocale.text("Приостановить выполнение цели", "Pause goal execution")))
            if (goal.status in setOf("paused", "blocked", "usage_limited")) add(SlashCommandSuggestion("/goal resume",
                AppLocale.text("Продолжить выполнение цели", "Resume goal execution")))
            add(SlashCommandSuggestion("/goal edit", AppLocale.text("Изменить цель: /goal edit новый текст", "Edit the goal: /goal edit new objective")))
            add(SlashCommandSuggestion("/goal clear", AppLocale.text("Убрать цель из этого чата", "Remove the goal from this chat")))
        }
    }
    return commands.filter { it.command.startsWith(query, ignoreCase = true) }
}
