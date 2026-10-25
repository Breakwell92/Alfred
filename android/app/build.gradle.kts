plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

val defaultServerUrl = providers.gradleProperty("alfred.serverUrl").get()

android {
    namespace = "com.alfred.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.alfred.app"
        minSdk = 29
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField("String", "DEFAULT_SERVER_URL", "\"$defaultServerUrl\"")

        // ONNX Runtime (wake word) ships ~35 MB of native code per ABI: keep 64-bit ARM phones
        // and the x86_64 emulator only (ADR 0019).
        ndk {
            abiFilters += setOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    lint {
        warningsAsErrors = false
        abortOnError = true
        // New library versions ship constantly; don't fail CI because one appeared.
        disable += setOf("GradleDependency", "NewerVersionAvailable", "AndroidGradlePluginVersion")
    }
}

// JDK 21 for both Java and Kotlin compilation (ADR 0004); no separate compileOptions to drift.
kotlin {
    jvmToolchain(21)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(platform(libs.koin.bom))
    implementation(libs.koin.android)
    implementation(libs.koin.androidx.compose)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.android)
    // openWakeWord models (ADR 0019).
    implementation(libs.onnxruntime.android)

    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    // A real org.json for JVM tests (android.jar's is a stub that throws).
    testImplementation(libs.json)
    // Desktop ONNX Runtime, so JVM tests can run the real wake word models.
    testImplementation(libs.onnxruntime.jvm)
}
