plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.nktkln.rackphone.companion"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.nktkln.rackphone.companion"
        // Oreo. Below it there is no `SubscriptionManager` worth calling and no
        // runtime permission model, and no phone this project would adopt runs
        // it. The two API guards in Sender.kt cover everything above.
        minSdk = 26
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // Debug keys, deliberately. This APK is installed over adb onto a
            // unit whose bootloader is already unlocked; a release keystore
            // would be one more secret to hold for no gain in trust. Replace it
            // if these units ever install from anywhere but a cable.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}

flutter {
    source = "../.."
}
