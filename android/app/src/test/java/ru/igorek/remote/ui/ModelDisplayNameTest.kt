package ru.igorek.remote.ui

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.igorek.remote.data.SessionOptionsResponse

class ModelDisplayNameTest {
    @Test fun unixWeightPathsShowTheFilenameAndKeepQuantization() {
        assertEquals("Ornith-122B-IQ2_XXS.gguf", modelDisplayName("/srv/models/Ornith/Ornith-122B-IQ2_XXS.gguf"))
        assertEquals("Bolt-74B-A9B-24GB.gguf", modelDisplayName("/srv/models/Bolt-74B-A9B-24GB.gguf"))
    }

    @Test fun windowsDriveAndNetworkPathsUseTheSameFilenamePresentation() {
        assertEquals("Bolt.gguf", modelDisplayName("C:\\AI\\models\\Bolt.gguf"))
        assertEquals("Bolt.gguf", modelDisplayName("C:/AI/models/Bolt.gguf"))
        assertEquals("Ornith.gguf", modelDisplayName("\\\\host\\models\\Ornith.gguf"))
    }

    @Test fun relativeAndHomeWeightPathsDoNotExposeDirectories() {
        for (path in listOf("./models/Ornith.gguf", "../models/Ornith.gguf", "~/AI/Ornith.gguf", "models/Ornith.gguf")) {
            assertEquals("Ornith.gguf", modelDisplayName(path))
        }
    }

    @Test fun cloudNamespacesAndModelAliasesStayIntact() {
        for (model in listOf("nvidia/nemotron-3.5-lightning:free", "openai/gpt-6.1", "gpt-6.1-sol", "mercury-3")) {
            assertEquals(model, modelDisplayName(model))
        }
    }

    @Test fun remoteUrlsAreNotMistakenForRelativeWeightFiles() {
        val model = "https://example.test/models/Ornith.gguf"
        assertEquals(model, modelDisplayName(model))
    }

    @Test fun fileUrisAndUnicodeFilenamesHaveReadableLabels() {
        assertEquals("Модель Q2.gguf", modelDisplayName("file:///srv/Модели/Модель Q2.gguf"))
        assertEquals("Bolt.GGUF", modelDisplayName("FILE:///C:/Models/Bolt.GGUF"))
    }

    @Test fun emptyValuesAndRootsDoNotCrash() {
        assertEquals("", modelDisplayName(""))
        assertEquals("", modelDisplayName("  "))
        assertEquals("/", modelDisplayName("/"))
        assertEquals("Ornith", modelDisplayName(" /srv/models/Ornith/ "))
    }

    @Test fun displayFormattingDoesNotRewriteTheProfileCatalogueOrWireModel() {
        val raw = "/srv/models/Ornith/Ornith-122B-IQ2_XXS.gguf"
        val json = Json { encodeDefaults = true }
        val options = json.decodeFromString<SessionOptionsResponse>(
            """{"profile_catalog":[{"name":"local-ornith","model":"$raw","local":true}],"profile_switch_supported":true}""")
        val choice = options.switchableProfiles.single()
        assertEquals("Ornith-122B-IQ2_XXS.gguf", modelDisplayName(choice.model))
        assertEquals(raw, choice.model)
        assertEquals("local-ornith", choice.name)
        assertEquals(options, json.decodeFromString<SessionOptionsResponse>(json.encodeToString(options)))
        assertTrue(json.encodeToString(options).contains(raw))
    }
}
