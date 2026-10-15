package ru.igorek.remote.data

import java.net.URI
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
internal data class PairingQr(
    val type: String,
    val version: Int,
    val url: String,
    val code: String,
    val fingerprint: String,
    @SerialName("expires_at") val expiresAt: Long,
    val name: String = "Десктоп",
) {
    override fun toString(): String = "PairingQr(url=$url, name=$name)"
}

internal fun decodePairingQr(raw: String, nowSeconds: Long = System.currentTimeMillis() / 1000): PairingQr? =
    runCatching {
        require(raw.length <= 4096)
        val value = Json.decodeFromString<PairingQr>(raw)
        require(value.type == "igorek.desktop.pair" && value.version == 1)
        require(value.name.length in 1..80)
        require(value.expiresAt in (nowSeconds - 30)..(nowSeconds + 600))
        require(Regex("[A-F0-9]{4}(?:-[A-F0-9]{4}){2}").matches(value.code))
        require(Regex("[A-Fa-f0-9]{64}").matches(value.fingerprint))
        val uri = URI(value.url)
        require(uri.scheme == "https" && !uri.host.isNullOrBlank())
        require(uri.userInfo == null && uri.rawQuery == null && uri.rawFragment == null)
        require(uri.path.isNullOrEmpty() || uri.path == "/")
        require(uri.port == -1 || uri.port in 1..65535)
        value.copy(url = value.url.trimEnd('/'), fingerprint = value.fingerprint.uppercase())
    }.getOrNull()
