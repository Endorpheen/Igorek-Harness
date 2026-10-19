import groovy.json.JsonSlurper
import java.nio.file.Files

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.kotlin.serialization)
}

val voiceMarkdownAssets = layout.buildDirectory.dir("generated/voice-markdown-assets")
val tlsTestResources = layout.buildDirectory.dir("generated/tls-test-resources")
val generateTestTlsKey = tasks.register<Exec>("generateTestTlsKey") {
    val output = tlsTestResources.map { it.file("loopback-test.p12") }
    outputs.file(output)
    doFirst {
        output.get().asFile.parentFile.mkdirs()
        output.get().asFile.delete()
    }
    commandLine(file("${System.getProperty("java.home")}/bin/keytool"),
        "-genkeypair", "-alias", "loopback", "-keyalg", "RSA", "-keysize", "2048",
        "-storetype", "PKCS12", "-keystore", output.get().asFile,
        "-storepass", "fixture-only", "-dname", "CN=localhost",
        "-ext", "SAN=ip:127.0.0.1", "-validity", "3650", "-noprompt")
}
tasks.withType<Test>().configureEach { dependsOn(generateTestTlsKey) }
tasks.matching { it.name.startsWith("process") && it.name.endsWith("UnitTestJavaRes") }
    .configureEach { dependsOn(generateTestTlsKey) }
val syncVoiceMarkdownRenderer = tasks.register<Sync>("syncVoiceMarkdownRenderer") {
    from(rootProject.file("../factory/web_ui/static/render.js"))
    from(file("src/main/res/drawable-nodpi/ic_igorek_octopus.jpg")) {
        rename { "igorek-octopus.jpg" }
    }
    into(voiceMarkdownAssets.map { it.dir("voice") })
}
tasks.named("preBuild") { dependsOn(syncVoiceMarkdownRenderer) }

android {
    sourceSets.getByName("test").resources.srcDir(rootProject.file("../tests/fixtures/diagnostics"))
    sourceSets.getByName("main").assets.directories.add(voiceMarkdownAssets.get().asFile.path)
    sourceSets.getByName("test").resources.directories.add(tlsTestResources.get().asFile.path)
    namespace = "ru.igorek.remote"
    compileSdk { version = release(37) { minorApiLevel = 0 } }

    defaultConfig {
        applicationId = "ru.igorek.remote"
        minSdk = 26
        targetSdk = 34
        testInstrumentationRunner = providers.gradleProperty("igorekTestRunner").orElse("ru.igorek.remote.VoiceCallInstrumentation").get()
        versionCode = 111
        versionName = "0.1.110"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val keyPath = providers.environmentVariable("IGOREK_ANDROID_KEYSTORE").orNull
            val passwordPath = providers.environmentVariable("IGOREK_ANDROID_STORE_PASSWORD_FILE").orNull
            val alias = providers.environmentVariable("IGOREK_ANDROID_KEY_ALIAS").orNull
            if (!keyPath.isNullOrBlank() && !passwordPath.isNullOrBlank() && !alias.isNullOrBlank()) {
                signingConfig = signingConfigs.create("publicationRelease") {
                    storeFile = file(keyPath)
                    storePassword = file(passwordPath).readText(Charsets.UTF_8).trim()
                    keyAlias = alias
                    keyPassword = storePassword
                    enableV1Signing = false
                    enableV2Signing = true
                    enableV3Signing = true
                }
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

val verifyReleaseSigning = tasks.register("verifyReleaseSigning") {
    doLast {
        listOf("IGOREK_ANDROID_KEYSTORE", "IGOREK_ANDROID_STORE_PASSWORD_FILE", "IGOREK_ANDROID_KEY_ALIAS").forEach {
            check(!providers.environmentVariable(it).orNull.isNullOrBlank()) { "Configure $it for a signed release." }
        }
        listOf("IGOREK_ANDROID_KEYSTORE", "IGOREK_ANDROID_STORE_PASSWORD_FILE").forEach {
            val path = file(providers.environmentVariable(it).get()).toPath()
            check(Files.isRegularFile(path) && !Files.isSymbolicLink(path)) { "Signing input must be a regular private file." }
            check(!path.toAbsolutePath().normalize().startsWith(rootProject.projectDir.parentFile.toPath().toAbsolutePath().normalize())) { "Keep signing credentials outside the repository." }
        }
    }
}
tasks.matching { it.name == "packageRelease" }.configureEach { dependsOn(verifyReleaseSigning) }

val verifyReleaseNotes = tasks.register("verifyReleaseNotes") {
    val notesFile = file("src/main/assets/release-notes.json")
    inputs.file(notesFile)
    inputs.property("versionName", provider { android.defaultConfig.versionName.orEmpty() })
    inputs.property("versionCode", provider { android.defaultConfig.versionCode ?: 0 })
    doLast {
        val notes = JsonSlurper().parseText(notesFile.readText(Charsets.UTF_8)) as Map<*, *>
        check(notes["versionName"] == android.defaultConfig.versionName &&
            (notes["versionCode"] as? Number)?.toInt() == android.defaultConfig.versionCode) {
            "Update src/main/assets/release-notes.json for this Android version before building."
        }
        val changes = notes["changes"] as? List<*>
        check(!changes.isNullOrEmpty()) { "Release notes must include at least one change." }
        changes.forEach { change ->
            val item = change as Map<*, *>
            listOf("ru", "en").forEach { language ->
                val translation = item[language] as Map<*, *>
                listOf("title", "text").forEach { field ->
                    check(!(translation[field] as? String).isNullOrBlank()) {
                        "Release notes must include $language.$field for each change."
                    }
                }
            }
        }
    }
}
tasks.named("preBuild") { dependsOn(verifyReleaseNotes) }

dependencies {
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.androidx.security.crypto)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
}
