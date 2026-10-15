package ru.igorek.remote.data

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

// Subscription quota for OpenCode Go in the mobile application.
//
// The same numbers as in the web panel: the server sends, per each window, already calculated
// usage percentage (percent), status, and reset timestamp (resetsAt, ISO-8601).
// This section contains only the core logic without Compose or Android-API to enable coverage
// with standard JVM tests.

internal const val QUOTA_CACHE_TTL_MS = 60_000L

internal enum class QuotaWindowKind(val labelRu: String, val labelEn: String) {
    Rolling("5 часов", "5 hours"),
    Weekly("Неделя", "Week"),
    Monthly("Месяц", "Month"),
}

internal enum class QuotaTone { Ok, Warn, Danger }

internal data class QuotaWindow(
    val kind: QuotaWindowKind,
    val percent: Double?,
    val status: String,
    val resetsAt: String,
)

// Subscription quota card state in the header: idle/loading/ready/failed.
internal sealed interface QuotaState {
    data object Idle : QuotaState
    data object Loading : QuotaState
    data class Ready(val usage: QuotaUsageDto, val fetchedAtMillis: Long, val provider: String = "opencode") : QuotaState
    data class Failed(val code: String) : QuotaState
}

internal fun quotaWindows(usage: QuotaUsageDto, provider: String = "opencode"): List<QuotaWindow> = listOf(
    QuotaWindow(QuotaWindowKind.Rolling, usage.rolling?.percent, usage.rolling?.status.orEmpty(), usage.rolling?.resetsAt.orEmpty()),
    QuotaWindow(QuotaWindowKind.Weekly, usage.weekly?.percent, usage.weekly?.status.orEmpty(), usage.weekly?.resetsAt.orEmpty()),
    QuotaWindow(QuotaWindowKind.Monthly, usage.monthly?.percent, usage.monthly?.status.orEmpty(), usage.monthly?.resetsAt.orEmpty()),
).filter { provider != "codex" || it.kind == QuotaWindowKind.Weekly }

// Threshold the same as in the web panel: 60% — warning, 85% and exhausted
// limit — red.
internal fun quotaTone(percent: Double?, status: String): QuotaTone = when {
    status.trim().lowercase() == "rate-limited" -> QuotaTone.Danger
    percent != null && percent >= 85 -> QuotaTone.Danger
    percent != null && percent >= 60 -> QuotaTone.Warn
    else -> QuotaTone.Ok
}

internal fun quotaPercentText(percent: Double?): String =
    if (percent == null) "—" else "${percent.roundToInt()}%"

internal fun quotaFreeText(percent: Double?): String =
    if (percent == null) "" else "${(100.0 - percent).coerceIn(0.0, 100.0).roundToInt()}% свободно"

internal fun quotaStatusText(status: String, language: AppLanguage): String {
    val value = status.trim().lowercase()
    if (value.isEmpty() || value == "ok") return ""
    if (value == "rate-limited") {
        return if (language == AppLanguage.Russian) "лимит исчерпан" else "limit reached"
    }
    return status.trim()
}

// Reset window duration: within a minute — 'just about to reset', within an hour — minutes,
// within two days — hours and minutes, otherwise — calendar date and time.
internal fun quotaResetText(
    resetsAt: String,
    nowMillis: Long,
    language: AppLanguage,
): String {
    val resetAt = parseInstantMillis(resetsAt) ?: return ""
    val delta = resetAt - nowMillis
    if (delta <= 60_000L) {
        return if (language == AppLanguage.Russian) "сброс вот-вот" else "resets shortly"
    }
    val minutes = ((delta + 59_999L) / 60_000L)
    if (minutes < 60) {
        return if (language == AppLanguage.Russian) "сброс через $minutes мин" else "resets in $minutes min"
    }
    val hours = minutes / 60
    val rest = minutes % 60
    if (hours < 48) {
        return if (language == AppLanguage.Russian) {
            if (rest > 0) "сброс через $hours ч $rest мин" else "сброс через $hours ч"
        } else {
            if (rest > 0) "resets in $hours h $rest min" else "resets in $hours h"
        }
    }
    val local = DateTimeFormatter.ofPattern("dd.MM HH:mm")
        .format(Instant.ofEpochMilli(resetAt).atZone(ZoneId.systemDefault()))
    return if (language == AppLanguage.Russian) "сброс $local" else "resets at $local"
}

internal fun parseInstantMillis(raw: String): Long? {
    val value = raw.trim()
    if (value.isEmpty()) return null
    return runCatching { Instant.parse(value).toEpochMilli() }.getOrNull()
}

// Error message texts for the quota match the web panel: the server sends machine code,
// while the client selects the phrasing to avoid mixing Russian and English.
internal val QUOTA_ERROR_CODES = listOf(
    "no_key",
    "key_rejected",
    "subscription_required",
    "rate_limited",
    "timeout",
    "network",
    "invalid_response",
    "upstream",
)

internal fun quotaErrorText(code: String?, language: AppLanguage): String {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when (code?.trim()?.lowercase()) {
        "quota_login_required" -> pick("Подключите счётчик Codex в десктопном Игорьке", "Connect the Codex counter in Igorek Desktop")
        "quota_codex_request_failed", "quota_codex_timeout" -> pick("Не удалось получить лимиты Codex", "Could not read Codex limits")
        "unsupported" -> pick("Для этого подключения счётчик недоступен", "Quota is unavailable for this connection")
        "no_key" -> pick("Ключ OpenCode не найден на компьютере", "No OpenCode key on the computer")
        "key_rejected" -> pick("Сервис квоты отклонил ключ", "The quota service rejected the key")
        "subscription_required" -> pick("Для квоты нужна подписка OpenCode Go", "An OpenCode Go subscription is required")
        "rate_limited" -> pick("Сервис квоты ограничил запросы", "The quota service is rate limiting")
        "timeout" -> pick("Сервис квоты не ответил вовремя", "The quota service timed out")
        "network" -> pick("Не удалось связаться с сервисом квоты", "Could not reach the quota service")
        "invalid_response" -> pick("Сервис квоты вернул неожиданный ответ", "The quota service returned an unexpected answer")
        "upstream" -> pick("Сервис квоты ответил ошибкой", "The quota service failed")
        else -> pick("Не удалось получить квоту", "Could not load the quota")
    }
}

internal fun quotaCacheFresh(fetchedAtMillis: Long, nowMillis: Long): Boolean =
    fetchedAtMillis > 0L && nowMillis - fetchedAtMillis < QUOTA_CACHE_TTL_MS
