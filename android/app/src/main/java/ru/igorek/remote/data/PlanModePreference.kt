package ru.igorek.remote.data

internal fun planModePreferenceKey(connectionId: String, baseUrl: String, sessionId: String): String {
    val scope = listOf(connectionId, baseUrl, sessionId).joinToString("") { "${it.length}:$it" }
    return "plan-mode:" + connectionScope(scope)
}
