package com.nktkln.rackphone.companion

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/**
 * Asking every SIM what its balance is, on a schedule.
 *
 * The keepalive protects a SIM from being reclaimed for inactivity. It does
 * nothing about the other way a prepaid SIM dies: the money runs out, the send
 * fails, and the only evidence is a `sent_failed` counter that ticks a month
 * after it mattered. A balance read daily turns that into something the host
 * can alert on before it happens.
 */
object Balance {
    private const val REQUEST_CODE = 0x2B1

    /** Never schedule inside this window, so a reschedule cannot become a loop. */
    private const val MIN_DELAY_MS = 60_000L

    fun nextRunAt(lastAtMs: Long, intervalMs: Long, nowMs: Long): Long =
        if (lastAtMs <= 0L) nowMs else lastAtMs + intervalMs

    /** (Re)arm the alarm for whichever SIM was read longest ago. */
    fun schedule(context: Context) {
        val config = Config.of(context)
        val alarms = context.getSystemService(AlarmManager::class.java) ?: return
        val intent = pendingIntent(context)

        if (config.balanceCode.isBlank()) {
            runCatching { alarms.cancel(intent) }
            return
        }

        val now = System.currentTimeMillis()
        val earliest = Sims.activeSubIds(context)
            .minOfOrNull { nextRunAt(config.balanceAt(it), config.balanceIntervalMs, now) }
            ?: (now + config.balanceIntervalMs)
        val at = maxOf(earliest, now + MIN_DELAY_MS)
        runCatching { alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent) }
    }

    /**
     * Read the balance on every SIM that is due, or on all of them when forced.
     *
     * @param onDone called once every SIM has answered or given up, with one
     *   entry per SIM asked.
     */
    fun refresh(context: Context, force: Boolean, onDone: (JSONArray) -> Unit) {
        val config = Config.of(context)
        val now = System.currentTimeMillis()
        val code = config.balanceCode

        val subs = Sims.activeSubIds(context).filter {
            force || nextRunAt(config.balanceAt(it), config.balanceIntervalMs, now) <= now
        }
        if (code.isBlank() || subs.isEmpty()) {
            schedule(context)
            return onDone(JSONArray())
        }

        val results = JSONArray()
        // One at a time: two USSD sessions at once is not something a modem
        // promises to handle, and there are never more than two SIMs.
        fun ask(index: Int) {
            if (index >= subs.size) {
                HostFiles.writeStatus(context)
                schedule(context)
                return onDone(results)
            }
            Ussd.request(context, code, subs[index]) { result ->
                results.put(result)
                ask(index + 1)
            }
        }
        ask(0)
    }

    private fun pendingIntent(context: Context): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            REQUEST_CODE,
            Intent(context, BalanceReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    /** What the host reads: one entry per SIM, newest reading kept. */
    fun report(context: Context): JSONArray {
        val config = Config.of(context)
        val out = JSONArray()
        for (sub in Sims.activeSubIds(context)) {
            val at = config.balanceAt(sub)
            if (at <= 0L) continue
            val entry = JSONObject()
                .put("sub_id", sub)
                .put("checked_ms", at)
                .put("text", config.balanceText(sub))
            config.balanceValue(sub)?.let { entry.put("amount", it) }
            out.put(entry)
        }
        return out
    }
}
