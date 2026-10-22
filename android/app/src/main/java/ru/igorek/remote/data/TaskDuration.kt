package ru.igorek.remote.data

internal fun taskDurationLabel(milliseconds: Long, language: AppLanguage): String {
    val seconds = milliseconds.coerceAtLeast(0) / 1000
    val ru = language == AppLanguage.Russian
    if (seconds == 0L) return if (ru) "менее 1 с" else "<1 s"
    val hours = seconds / 3600
    val minutes = seconds % 3600 / 60
    val remaining = seconds % 60
    val parts = mutableListOf<String>()
    if (hours > 0) parts += if (ru) "$hours ч" else "$hours h"
    if (minutes > 0) parts += if (ru) "$minutes мин" else "$minutes min"
    if (remaining > 0 || parts.isEmpty()) parts += if (ru) "$remaining с" else "$remaining s"
    return parts.joinToString(" ")
}
