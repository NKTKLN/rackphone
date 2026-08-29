package com.nktkln.rackphone.companion

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * The periodic message that keeps a SIM from being reclaimed.
 *
 * Operators expire a SIM that has not been used - some count any billable
 * event, some specifically an outgoing SMS - and a unit in a rack generates
 * neither. So the app sends one message on a schedule it owns itself.
 *
 * On-device scheduling, not a host cron, because the SIM is lost the same way
 * whether the host was unplugged, reinstalled or simply forgotten. The unit has
 * to be able to keep itself alive with nothing attached to it.
 *
 * Everything here is per subscription. An operator sees only its own SIM, so a
 * dual-SIM unit has two independent clocks: traffic on one says nothing about
 * the other, and the SIM that is quietly dying is exactly the one a device-wide
 * schedule would hide.
 */
object Keepalive {
    private const val REQUEST_CODE = 0x1F5

    /**
     * How long after an attempt that did not succeed to try again.
     *
     * A day, because this also bounds the case where the radio takes a message
     * and never reports back: that send is real airtime the operator saw, but
     * this app cannot know it, so the retry has to be slow enough that being
     * wrong about it costs one message rather than four.
     */
    private const val RETRY_MS = 24 * 3_600_000L

    /** An alarm never fires early by much; this absorbs the rest. */
    private const val SLACK_MS = 60_000L

    /** Never schedule inside this window, so a reschedule cannot become a loop. */
    private const val MIN_DELAY_MS = 60_000L

    /**
     * When the next message is due on the schedule alone.
     *
     * A SIM that has never sent is due one full interval from now rather than
     * immediately: enabling a setting should not spend money and airtime before
     * the person who enabled it has finished reading the screen. Sending on
     * demand is what the `KEEPALIVE` command is for.
     */
    fun nextDueAt(lastSuccessMs: Long, intervalMs: Long, nowMs: Long): Long =
        if (lastSuccessMs <= 0L) nowMs + intervalMs else lastSuccessMs + intervalMs

    /** When a subscription should actually run, once retry backoff is folded in. */
    fun nextRunAt(
        lastSuccessMs: Long,
        lastAttemptMs: Long,
        intervalMs: Long,
        nowMs: Long,
    ): Long {
        val due = nextDueAt(lastSuccessMs, intervalMs, nowMs)
        val retry = if (lastAttemptMs > 0L) lastAttemptMs + RETRY_MS else 0L
        return maxOf(due, retry)
    }

    /**
     * Resolve the `keepalive_subs` setting against the SIMs actually present.
     *
     * A configured subscription that is not present is dropped rather than
     * attempted: a SIM can be pulled, and sending "keep this alive" through
     * whichever subscription inherited the slot would be worse than sending
     * nothing.
     *
     * @param setting `all`, `default`, or a comma-separated list of ids.
     * @param active Subscription ids the modem reports, empty when unreadable.
     * @param default The subscription Android would send from.
     */
    fun subsFor(setting: String, active: List<Int>, default: Int): List<Int> {
        val wanted = setting.trim().lowercase()
        if (wanted.isEmpty() || wanted == Config.SUBS_ALL) {
            // Without READ_PHONE_STATE the list is empty and the modem still
            // sends: fall back to whatever Android calls the default.
            return active.ifEmpty { listOf(default) }
        }
        if (wanted == Config.SUBS_DEFAULT) return listOf(default)

        val listed = wanted.split(",").mapNotNull { it.trim().toIntOrNull() }.distinct()
        return if (active.isEmpty()) listed else listed.filter { it in active }
    }

    /** The subscriptions this unit is configured to keep alive, right now. */
    fun subs(context: Context): List<Int> =
        subsFor(
            Config.of(context).keepaliveSubs,
            Sims.activeSubIds(context),
            Sims.defaultSubId(),
        )

    /** (Re)arm the alarm for whichever subscription is due first. */
    fun schedule(context: Context) {
        val config = Config.of(context)
        val alarms = context.getSystemService(AlarmManager::class.java) ?: return
        val intent = pendingIntent(context)

        if (!config.keepaliveEnabled) {
            runCatching { alarms.cancel(intent) }
            return
        }

        val now = System.currentTimeMillis()
        val earliest = subs(context).minOfOrNull { runAtFor(config, it, now) }
            ?: (now + config.keepaliveIntervalMs)
        val at = maxOf(earliest, now + MIN_DELAY_MS)
        // Inexact and idle-tolerant on purpose: a message that is a fortnight
        // early or an hour late serves the same purpose, and asking for an
        // exact alarm would mean asking the operator for a permission the unit
        // has nobody to grant it.
        runCatching { alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent) }
    }

    fun cancel(context: Context) {
        val alarms = context.getSystemService(AlarmManager::class.java) ?: return
        runCatching { alarms.cancel(pendingIntent(context)) }
    }

    /**
     * Run the keepalive, one message per subscription that is due.
     *
     * @param force send on every configured SIM whatever the schedule says, for
     *   the `KEEPALIVE` command and the button in the UI. This is the only way
     *   to prove the path works without waiting out an interval.
     * @return what was sent, per subscription.
     */
    fun fire(context: Context, force: Boolean): JSONObject {
        val config = Config.of(context)
        val now = System.currentTimeMillis()

        if (!force && !config.keepaliveEnabled) {
            cancel(context)
            return JSONObject().put("status", "disabled")
        }

        val sent = JSONArray()
        for (sub in subs(context)) {
            if (!force && now + SLACK_MS < runAtFor(config, sub, now)) continue
            sent.put(sendFor(context, config, sub, now))
        }

        schedule(context)
        return JSONObject()
            .put("status", if (sent.length() == 0) "not_due" else "sent")
            .put("sent", sent)
    }

    private fun sendFor(
        context: Context,
        config: Config,
        sub: Int,
        nowMs: Long,
    ): JSONObject {
        // Recorded before the attempt, not after: a send that kills the process
        // must still cost this SIM its retry interval.
        config.recordAttempt(sub, nowMs)

        val request = SendRequest(
            to = targetFor(context, sub) ?: "",
            body = config.keepaliveBody,
            subId = sub,
            source = Commands.SOURCE_KEEPALIVE,
            id = "ka-" + UUID.randomUUID().toString().take(8),
        )
        return if (request.to.isEmpty()) {
            Sender.reject(context, request, "no_target")
        } else {
            Sender.send(context, request)
        }
    }

    /**
     * The number to text for one subscription.
     *
     * `self` resolves through that SIM, which only knows its own number if the
     * operator wrote it there. When it does not, this returns null and the
     * attempt is recorded as `no_target` - visible in `status.json` - rather
     * than quietly doing nothing, because "the SIM is being kept alive" is
     * exactly the belief that must not be false.
     */
    fun targetFor(context: Context, sub: Int): String? {
        val configured = Config.of(context).keepaliveTo.trim()
        if (configured.isEmpty() || configured.equals(Config.TARGET_SELF, ignoreCase = true)) {
            return Sims.selfNumber(context, sub)
        }
        return Numbers.sanitise(configured)
    }

    /** When one subscription is next due, schedule and backoff together. */
    fun runAtFor(config: Config, sub: Int, nowMs: Long): Long =
        nextRunAt(
            config.lastSuccessMs(sub),
            config.lastAttemptMs(sub),
            config.keepaliveIntervalMs,
            nowMs,
        )

    private fun pendingIntent(context: Context): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            REQUEST_CODE,
            Intent(context, KeepaliveReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
}
