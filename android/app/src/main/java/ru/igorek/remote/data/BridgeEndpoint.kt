package ru.igorek.remote.data

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

internal fun secureBridgeUrl(raw: String): String {
    val input = raw.trim()
    val url = (if (input.contains("://")) input else "https://$input").toHttpUrlOrNull()
    require(url != null && url.isHttps && url.username.isEmpty() && url.password.isEmpty()
        && url.query == null && url.fragment == null) {
        "Для подключения требуется HTTPS. Используйте QR-код десктопа."
    }
    return url.toString().trimEnd('/')
}
