package ru.igorek.remote.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.SANDBOX_REASON_BWRAP_MISSING
import ru.igorek.remote.data.SANDBOX_REASON_ENV_DISABLED
import ru.igorek.remote.data.SANDBOX_REASON_NAMESPACE_BLOCKED
import ru.igorek.remote.data.SANDBOX_REASON_OBSERVE
import ru.igorek.remote.data.SANDBOX_REASON_USER_DISABLED
import ru.igorek.remote.data.SandboxSettingsResponse
import ru.igorek.remote.data.SecuritySettingsResponse
import ru.igorek.remote.data.normalizeSecurityLevel
import ru.igorek.remote.data.sandboxReasonCode
import ru.igorek.remote.data.securityExpiry
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

internal val LocalAppLanguage = staticCompositionLocalOf { AppLanguage.Russian }

@Composable
internal fun uiText(russian: String, english: String): String =
    if (LocalAppLanguage.current == AppLanguage.Russian) russian else english

internal fun securityLevelLabel(level: String, language: AppLanguage = AppLocale.current): String {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when (normalizeSecurityLevel(level)) {
        "strict" -> pick("Строгий", "Strict")
        "trusted" -> pick("Доверенный", "Trusted")
        "full" -> pick("Полный доступ", "Full access")
        else -> pick("Стандартный", "Standard")
    }
}

internal fun sandboxReasonLabel(reason: String, language: AppLanguage = AppLocale.current): String {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when (sandboxReasonCode(reason)) {
        SANDBOX_REASON_BWRAP_MISSING -> pick("bwrap не установлен", "bwrap is not installed")
        SANDBOX_REASON_NAMESPACE_BLOCKED -> pick("namespaces запрещены", "namespaces are blocked")
        SANDBOX_REASON_ENV_DISABLED -> pick("отключена настройкой сервера", "disabled by server configuration")
        SANDBOX_REASON_USER_DISABLED -> pick("отключена в настройках", "disabled in settings")
        "" -> pick("причина неизвестна", "unknown reason")
        else -> reason.trim()
    }
}

internal fun sandboxStatusLabel(
    settings: SandboxSettingsResponse,
    language: AppLanguage = AppLocale.current,
): String {
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    val reason = sandboxReasonCode(settings.reason)
    val unavailable = !settings.available ||
        reason == SANDBOX_REASON_BWRAP_MISSING ||
        reason == SANDBOX_REASON_NAMESPACE_BLOCKED
    val disabledByReason = reason == SANDBOX_REASON_USER_DISABLED || reason == SANDBOX_REASON_ENV_DISABLED
    val detail = sandboxReasonLabel(reason, language) + if (reason.isEmpty()) "" else " ($reason)"
    return when {
        reason == SANDBOX_REASON_OBSERVE -> pick("Режим наблюдения", "Observe mode")
        unavailable -> pick("Недоступна: $detail", "Unavailable: $detail")
        !settings.enabled || disabledByReason || !settings.active -> pick("Отключена", "Disabled")
        else -> pick(
            "Активна — запись только в разрешённые папки",
            "Active — writes only to allowed folders",
        )
    }
}

internal fun securityExpiryText(
    settings: SecuritySettingsResponse,
    language: AppLanguage = AppLocale.current,
    nowSeconds: Long = System.currentTimeMillis() / 1000,
): String? {
    val expiresAt = settings.expiresAt ?: return null
    val expiry = securityExpiry(expiresAt, nowSeconds) ?: return null
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    if (expiry.expired) return pick("срок истёк", "expired")
    val clock = DateTimeFormatter.ofPattern("HH:mm")
        .format(Instant.ofEpochSecond(expiresAt).atZone(ZoneId.systemDefault()))
    return if (expiry.minutesLeft >= 60) {
        pick(
            "осталось ~${expiry.minutesLeft / 60} ч (до $clock)",
            "~${expiry.minutesLeft / 60} h left (until $clock)",
        )
    } else {
        pick(
            "осталось ~${expiry.minutesLeft} мин (до $clock)",
            "~${expiry.minutesLeft} min left (until $clock)",
        )
    }
}
