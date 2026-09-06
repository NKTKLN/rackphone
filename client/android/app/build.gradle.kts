import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.nktkln.rackphone.client"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // flutter_local_notifications schedules against java.time, which API 26
        // predates in part. Without this the build fails outright rather than
        // degrading, so it is a requirement of that dependency, not a choice.
        isCoreLibraryDesugaringEnabled = true
    }

    defaultConfig {
        applicationId = "com.nktkln.rackphone.client"
        // Matches the companion: API 26 is where the Keystore-backed storage and
        // the notification behaviour this app relies on are dependable.
        minSdk = 26
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        // A release identity, when one exists. `android/key.properties` is not
        // tracked and holds the store path and its passwords; without it the
        // build below falls back to the debug key so `flutter run --release`
        // still works on your own device.
        create("release") {
            val properties = rootProject.file("key.properties")
            if (properties.exists()) {
                val key = Properties()
                properties.inputStream().use(key::load)
                storeFile = file(key.getProperty("storeFile"))
                storePassword = key.getProperty("storePassword")
                keyAlias = key.getProperty("keyAlias")
                keyPassword = key.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // Debug-signed until a key exists. That is fine for running on the
            // phone in your hand and wrong for anything installed anywhere
            // else: an app cannot later be upgraded in place across a change of
            // signing identity, so distributing a debug-signed build is a
            // decision that outlives the build.
            signingConfig =
                if (rootProject.file("key.properties").exists()) {
                    signingConfigs.getByName("release")
                } else {
                    signingConfigs.getByName("debug")
                }
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
    testImplementation("junit:junit:4.13.2")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
