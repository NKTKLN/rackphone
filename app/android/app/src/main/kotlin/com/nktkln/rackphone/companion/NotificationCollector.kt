package com.nktkln.rackphone.companion

import android.app.Notification
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject

/**
 * App notifications, captured at the supported system boundary.
 *
 * The cache remembers only the latest content for a bounded number of keys.
 * Notification keys can be created forever on a unit that never reboots, so an
 * unbounded map here would be a process-lifetime leak.
 */
class NotificationCollector : NotificationListenerService() {
    private val dedup = NotificationDeduplicator(DEDUP_CAP)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val config = Config.of(this)
        // Off means no notification content is deliberately retained or sent.
        if (!config.collectNotifications) return
        // Recording our own status notifications would make the collector feed itself.
        if (isOwnPackage(sbn.packageName, packageName)) return

        val flags = sbn.notification.flags
        // Progress and other ongoing notifications update repeatedly for one event.
        if (hasFlag(flags, Notification.FLAG_ONGOING_EVENT)) return
        // A group summary duplicates the child notifications already delivered.
        if (hasFlag(flags, Notification.FLAG_GROUP_SUMMARY)) return

        val extras = sbn.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString().orEmpty()
        // Apps update notifications in place; unchanged content is the same event.
        if (dedup.isDuplicate(sbn.key, title, text)) return

        val event = JSONObject()
            .put("kind", "notification")
            .put("address", sbn.packageName)
            .put("app", appLabel(sbn.packageName))
            .put("title", title)
            .put("ts", sbn.postTime)
            .put("body", if (config.includeBody) text else JSONObject.NULL)

        Inbox.record(this, event)
        HostFiles.writeStatus(this)
    }

    private fun appLabel(packageName: String): String =
        try {
            val info = packageManager.getApplicationInfo(packageName, 0)
            packageManager.getApplicationLabel(info).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            ""
        }

    private companion object {
        const val DEDUP_CAP = 256
    }
}

/** Package equality is kept Android-free so the recursion rule is testable. */
fun isOwnPackage(sender: String, ownPackage: String): Boolean = sender == ownPackage

/** Flag matching is kept Android-free so each skip rule is testable on the JVM. */
fun hasFlag(flags: Int, flag: Int): Boolean = flags and flag != 0

/** Latest title and text observed for one system notification key. */
data class NotificationContent(val title: String, val text: String)

/**
 * Detect unchanged reposts without retaining every notification key ever seen.
 * Access order keeps actively updated notifications while evicting stale keys.
 */
class NotificationDeduplicator(private val capacity: Int) {
    init {
        require(capacity > 0)
    }

    private val latest = LinkedHashMap<String, NotificationContent>(capacity, 0.75f, true)

    fun isDuplicate(key: String, title: String, text: String): Boolean {
        val content = NotificationContent(title, text)
        val duplicate = latest[key] == content
        latest[key] = content
        if (latest.size > capacity) latest.remove(latest.entries.first().key)
        return duplicate
    }

    /** Visible for tests that prove the process-lifetime cache stays bounded. */
    fun size(): Int = latest.size
}
