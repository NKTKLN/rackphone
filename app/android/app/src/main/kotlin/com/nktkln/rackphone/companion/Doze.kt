package com.nktkln.rackphone.companion

import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.PowerManager

/**
 * Whether the system will actually run this app's alarms.
 *
 * A rack unit is a phone nobody ever opens, which is exactly the profile
 * Android treats as abandoned: it drops the app into the lowest standby bucket
 * and defers its alarms - in the case that prompted this file, from 28 days to
 * 364. The schedule looked healthy the whole time, and `next_due` was honest
 * about what was asked for and silent about what would happen.
 *
 * So the app reports the two things that decide it. Both are set from the host
 * at install time, without a tap:
 *
 *   adb shell dumpsys deviceidle whitelist +com.nktkln.rackphone.companion
 *   adb shell am set-standby-bucket com.nktkln.rackphone.companion active
 */
object Doze {

    /** Whether battery optimisation is off for this app, which is what lifts the deferral. */
    fun isExempt(context: Context): Boolean {
        val power = context.getSystemService(PowerManager::class.java) ?: return false
        return runCatching { power.isIgnoringBatteryOptimizations(context.packageName) }
            .getOrDefault(false)
    }

    /**
     * The app's own standby bucket, as a word. Querying another app's bucket
     * needs a permission; querying your own does not.
     */
    fun standbyBucket(context: Context): String {
        val usage = context.getSystemService(UsageStatsManager::class.java)
            ?: return "unknown"
        val bucket = runCatching { usage.appStandbyBucket }.getOrNull() ?: return "unknown"
        return when {
            bucket <= UsageStatsManager.STANDBY_BUCKET_ACTIVE -> "active"
            bucket <= UsageStatsManager.STANDBY_BUCKET_WORKING_SET -> "working_set"
            bucket <= UsageStatsManager.STANDBY_BUCKET_FREQUENT -> "frequent"
            bucket <= UsageStatsManager.STANDBY_BUCKET_RARE -> "rare"
            else -> "restricted"
        }
    }
}
