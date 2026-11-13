package ru.igorek.remote.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class VaultAccess(
    val supported: Boolean = true,
    val state: String = "",
    @SerialName("unlock_available") val unlockAvailable: Boolean = false,
) {
    val requiresUnlock: Boolean get() = supported && state != "unlocked"
}

internal fun vaultUnlockFailure(error: Throwable): String = when ((error as? BridgeException)?.code) {
    "authentication_failed" -> "incorrect_code"
    "unlock_rate_limited" -> "rate_limited"
    "vault_setup_required" -> "setup_required"
    else -> "unavailable"
}
