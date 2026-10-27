package ru.igorek.remote.data

enum class LocalChatCommand { Help, Status }

fun parseLocalChatCommand(text: String): LocalChatCommand? = when (text.trim()) {
    "/help" -> LocalChatCommand.Help
    "/status" -> LocalChatCommand.Status
    else -> null
}

fun chatStatusLines(sessionId: String, cwd: String, model: String, usage: ContextUsage,
                    reportedWindow: Long?, weeklyUsedPercent: Double?): List<Pair<String, String>> {
    val unknown = AppLocale.text("Нет данных", "Unavailable")
    val window = usage.effectiveWindow(reportedWindow)
    val used = usage.lastInput
    val context = if (window != null && used != null) {
        val remaining = (window - used).coerceAtLeast(0)
        AppLocale.text("Осталось $remaining из $window токенов", "$remaining of $window tokens remaining")
    } else unknown
    val quota = weeklyUsedPercent?.takeIf { it.isFinite() }?.let {
        val remaining = (100.0 - it).coerceIn(0.0, 100.0).toInt()
        AppLocale.text("Осталось $remaining%", "$remaining% remaining")
    } ?: unknown
    return listOf(
        AppLocale.text("Чат", "Chat") to sessionId,
        AppLocale.text("Каталог", "Directory") to cwd.ifBlank { unknown },
        AppLocale.text("Модель", "Model") to model.ifBlank { unknown },
        AppLocale.text("Контекст", "Context") to context,
        AppLocale.text("Лимит за неделю", "Weekly limit") to quota,
    )
}
