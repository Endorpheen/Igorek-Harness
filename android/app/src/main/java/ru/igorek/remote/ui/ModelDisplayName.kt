package ru.igorek.remote.ui

internal fun modelDisplayName(model: String): String {
    val value = model.trim()
    val path = value.replace('\\', '/')
    val drivePath = path.length >= 3 && path[0].isLetter() && path[1] == ':' && path[2] == '/'
    val localPath = path.startsWith('/') || drivePath || path.startsWith("~/") ||
        path.startsWith("./") || path.startsWith("../") || path.startsWith("file://", ignoreCase = true) ||
        (!path.contains("://") && path.endsWith(".gguf", ignoreCase = true))
    return if (localPath) path.trimEnd('/').substringAfterLast('/').ifBlank { value } else value
}
