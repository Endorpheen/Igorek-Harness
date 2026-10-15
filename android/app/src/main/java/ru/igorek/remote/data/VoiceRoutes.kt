package ru.igorek.remote.data

internal fun voiceRoute(sessionId: String, path: String): String {
    require(sessionId.matches(Regex("[A-Za-z0-9_-]+")))
    if (path in setOf("/api/voice/config", "/api/voice/session", "/api/voice/sdp", "/api/voice/hangup")) return path.removePrefix("/api")
    if (path == "/api/sessions/$sessionId/messages") return path.removePrefix("/api")
    if (path.matches(Regex("/api/tasks/[A-Za-z0-9_-]+/approvals/[A-Za-z0-9_-]+"))) return path.removePrefix("/api")
    throw IllegalArgumentException("Unsupported voice request")
}
