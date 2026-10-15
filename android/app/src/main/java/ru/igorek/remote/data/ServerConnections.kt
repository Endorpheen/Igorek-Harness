package ru.igorek.remote.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

@Serializable
internal data class ServerConnection(
    val id: String,
    val label: String,
    val url: String,
    val token: String,
    val fingerprint: String = "",
    val deviceName: String = "",
    val lastSeq: Long = 0,
    val profile: String = "",
    val cwd: String = "",
)

data class SavedConnection(val id: String, val label: String, val url: String, val active: Boolean)

internal object ConnectionCodec {
    private val json = Json { ignoreUnknownKeys = true }
    fun decode(value: String): List<ServerConnection> =
        runCatching { json.decodeFromString<List<ServerConnection>>(value) }.getOrDefault(emptyList())
    fun encode(value: List<ServerConnection>): String = json.encodeToString(value)
    fun upsert(values: List<ServerConnection>, current: ServerConnection): List<ServerConnection> =
        values.filterNot { it.id == current.id || it.url == current.url } + current
}
