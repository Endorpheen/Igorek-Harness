package ru.igorek.remote.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences

class Prefs(context: Context) {

    @Suppress("DEPRECATION")
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        FILE_NAME,
        MASTER_KEY_ALIAS,
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_BASE_URL, value).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TOKEN, value).apply()

    var fingerprint: String
        get() = prefs.getString(KEY_FINGERPRINT, "") ?: ""
        set(value) = prefs.edit().putString(KEY_FINGERPRINT, value).apply()

    var deviceName: String
        get() = prefs.getString(KEY_DEVICE_NAME, "") ?: ""
        set(value) = prefs.edit().putString(KEY_DEVICE_NAME, value).apply()

    var lastSeq: Long
        get() = prefs.getLong(KEY_LAST_SEQ, 0L)
        set(value) = prefs.edit().putLong(KEY_LAST_SEQ, value).apply()

    var notificationsEnabled: Boolean
        get() = prefs.getBoolean(KEY_NOTIFICATIONS, true)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFICATIONS, value).apply()

    var screenshotsBlocked: Boolean
        get() = prefs.getBoolean(KEY_SCREENSHOTS_BLOCKED, false)
        set(value) = prefs.edit().putBoolean(KEY_SCREENSHOTS_BLOCKED, value).apply()

    var lastSessionProfile: String
        get() = prefs.getString(KEY_LAST_SESSION_PROFILE, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LAST_SESSION_PROFILE, value).apply()

    var lastSessionCwd: String
        get() = prefs.getString(KEY_LAST_SESSION_CWD, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LAST_SESSION_CWD, value).apply()

    var appLanguage: String
        get() = prefs.getString(KEY_APP_LANGUAGE, AppLanguage.Russian.code) ?: AppLanguage.Russian.code
        set(value) = prefs.edit().putString(KEY_APP_LANGUAGE, value).apply()

    internal fun mcpChat(sessionId: String): String = prefs.getString(mcpChatKey(sessionId), "") ?: ""

    internal fun saveMcpChat(sessionId: String, encoded: String) {
        check(prefs.edit().putString(mcpChatKey(sessionId), encoded).commit()) { "MCP history could not be saved" }
    }

    private fun mcpChatKey(sessionId: String): String {
        val scope = "$baseUrl\u0000$token\u0000$sessionId"
        val digest = java.security.MessageDigest.getInstance("SHA-256").digest(scope.toByteArray(Charsets.UTF_8))
        return "mcp_chat:" + digest.joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    var activeConnectionId: String
        get() = prefs.getString("active_connection", "").orEmpty()
        set(value) = prefs.edit().putString("active_connection", value).apply()

    internal fun connection(id: String): ServerConnection? =
        ConnectionCodec.decode(prefs.getString("server_connections", "[]").orEmpty()).find { it.id == id }

    internal var notificationHistory: String
        get() = prefs.getString("diagnostics:"+activeConnectionId+":"+connectionScope(baseUrl), "[]").orEmpty().take(131072)
        set(value) {
            require(value.length<=131072)
            check(prefs.edit().putString("diagnostics:"+activeConnectionId+":"+connectionScope(baseUrl),value).commit()) { "Diagnostic history could not be saved" }
        }

    internal var pendingOperationKeys: String
        get() = prefs.getString("pending-operations:"+activeConnectionId+":"+connectionScope(baseUrl),"{}").orEmpty().take(16000)
        set(value) {
            require(value.length<=16000)
            check(prefs.edit().putString("pending-operations:"+activeConnectionId+":"+connectionScope(baseUrl),value).commit()) { "Pending operation keys could not be saved" }
        }

    fun savedConnections(): List<SavedConnection> =
        ConnectionCodec.decode(prefs.getString("server_connections", "[]").orEmpty()).map {
            SavedConnection(it.id, it.label, it.url, it.id == activeConnectionId)
        }

    fun saveCurrentConnection(label: String? = null) {
        if (!isPaired()) return
        val values = ConnectionCodec.decode(prefs.getString("server_connections", "[]").orEmpty())
        val existing = values.find { it.id == activeConnectionId }
        val id = existing?.id ?: java.util.UUID.randomUUID().toString()
        val current = ServerConnection(id, label?.trim()?.takeIf { it.isNotEmpty() }
            ?: existing?.label ?: "Web UI", baseUrl, token, fingerprint, deviceName, lastSeq,
            lastSessionProfile, lastSessionCwd)
        check(prefs.edit().putString("server_connections", ConnectionCodec.encode(ConnectionCodec.upsert(values, current)))
            .putString("active_connection", id).commit()) { "Connection could not be saved" }
    }

    fun activateConnection(id: String) {
        val saved = connection(id) ?: error("Unknown connection")
        check(prefs.edit().putString(KEY_BASE_URL, saved.url).putString(KEY_TOKEN, saved.token)
            .putString(KEY_FINGERPRINT, saved.fingerprint).putString(KEY_DEVICE_NAME, saved.deviceName)
            .putLong(KEY_LAST_SEQ, saved.lastSeq).putString(KEY_LAST_SESSION_PROFILE, saved.profile)
            .putString(KEY_LAST_SESSION_CWD, saved.cwd).putString("active_connection", id).commit()) {
            "Connection could not be selected"
        }
    }

    fun isPaired(): Boolean = token.isNotEmpty() && baseUrl.isNotEmpty()

    fun clear() {
        val language = appLanguage
        val screenshotProtection = screenshotsBlocked
        prefs.edit().clear().putString(KEY_APP_LANGUAGE, language)
            .putBoolean(KEY_SCREENSHOTS_BLOCKED, screenshotProtection).apply()
    }

    private companion object {
        const val FILE_NAME = "igorek_remote"
        const val MASTER_KEY_ALIAS = "igorek_remote_master_key"
        const val KEY_BASE_URL = "base_url"
        const val KEY_TOKEN = "device_token"
        const val KEY_FINGERPRINT = "pinned_fingerprint"
        const val KEY_DEVICE_NAME = "device_name"
        const val KEY_LAST_SEQ = "last_seq"
        const val KEY_NOTIFICATIONS = "notifications"
        const val KEY_SCREENSHOTS_BLOCKED = "screenshots_blocked"
        const val KEY_LAST_SESSION_PROFILE = "last_session_profile"
        const val KEY_LAST_SESSION_CWD = "last_session_cwd"
        const val KEY_APP_LANGUAGE = "app_language"
    }
}
