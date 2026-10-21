package ru.igorek.remote.data

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProfileChoicesTest {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    @Test
    fun legacyServerKeepsItsExistingProfileChoices() {
        val value = json.decodeFromString<SessionOptionsResponse>("""{"profiles":["codexpro"],"default_profile":"codexpro"}""")
        assertEquals(listOf("codexpro"), value.selectableProfiles)
        assertFalse(value.profileSwitchSupported)
        assertTrue(value.switchableProfiles.isEmpty())
    }

    @Test
    fun verifiedCatalogAddsLocalModelsWithoutDuplicatingTheActiveCloudProfile() {
        val value = json.decodeFromString<SessionOptionsResponse>("""{"profiles":["codexpro"],"profile_catalog":[{"name":"codexpro","model":"cloud-model","local":false},{"name":"local-lfm25-128k","model":"lfm2.5-24b-a2b","local":true}],"profile_switch_supported":true}""")
        assertEquals(listOf("codexpro", "local-lfm25-128k"), value.selectableProfiles)
        assertTrue(value.profileCatalog.last().local)
        assertTrue(value.profileSwitchSupported)
        assertEquals(value.profileCatalog, value.switchableProfiles)
    }

    @Test fun switchingUsesOnlyTheVerifiedCatalogWhenSupported() {
        val choices = listOf(ProfileChoice("local", "model"), ProfileChoice("local", "duplicate"), ProfileChoice(""))
        val options = SessionOptionsResponse(profiles = listOf("legacy"), profileCatalog = choices, profileSwitchSupported = true)
        assertEquals(listOf(ProfileChoice("local", "model")), options.switchableProfiles)
        assertTrue(options.copy(profileSwitchSupported = false).switchableProfiles.isEmpty())
    }

    @Test
    fun profileActivationIsAnExplicitRequestFlag() {
        assertTrue(json.encodeToString(CreateSessionRequest("local-lfm25-128k", "/project", "fixture", activateProfile = true)).contains("\"activate_profile\":true"))
        assertTrue(json.encodeToString(SendMessageRequest("test", activateProfile = true)).contains("\"activate_profile\":true"))
        assertFalse(CreateSessionRequest(idempotencyKey = "legacy").activateProfile)
        assertFalse(SendMessageRequest("legacy").activateProfile)
    }
}
