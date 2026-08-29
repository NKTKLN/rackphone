package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import org.json.JSONObject

/**
 * Incoming calls, reconstructed from the phone state.
 *
 * Android never announces "a call was missed". It announces ringing, off-hook
 * and idle, and a missed call is the shape those make: ringing, then idle, with
 * nothing in between. That shape is tracked across broadcasts in
 * SharedPreferences, because each one arrives at a process that may have been
 * started for it and killed straight after.
 *
 * The limit of this route is honest and worth stating: it sees the phone state,
 * not the call log, so a call the network or a blocklist rejected looks exactly
 * like one nobody answered, and both are reported as missed. On an unattended
 * unit the actionable fact - somebody called and got no reply - is the same.
 *
 * An outgoing call goes off-hook without ringing first, so nothing is recorded
 * for it: this relay is about what arrives at the unit.
 */
class CallReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return
        val config = Config.of(context)
        if (!config.collectCalls) return

        when (intent.getStringExtra(TelephonyManager.EXTRA_STATE)) {
            TelephonyManager.EXTRA_STATE_RINGING -> startRinging(context, intent, config)
            TelephonyManager.EXTRA_STATE_OFFHOOK -> {
                // Only meaningful for a call we already saw ringing; for an
                // outgoing call there is nothing to mark as answered.
                if (config.ringingFrom.isNotEmpty() && config.callAnsweredMs == 0L) {
                    config.callAnsweredMs = System.currentTimeMillis()
                }
            }
            TelephonyManager.EXTRA_STATE_IDLE -> finish(context, config)
        }
    }

    private fun startRinging(context: Context, intent: Intent, config: Config) {
        @Suppress("DEPRECATION")
        val number = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER).orEmpty()
        // The number is withheld without READ_CALL_LOG, and by a caller who
        // hides it. "unknown" keeps the event - that a call came in at all is
        // the part worth relaying.
        config.ringingFrom = Numbers.sanitise(number) ?: UNKNOWN_CALLER
        config.ringingSinceMs = System.currentTimeMillis()
        config.callAnsweredMs = 0L
        HostFiles.writeStatus(context)
    }

    private fun finish(context: Context, config: Config) {
        val from = config.ringingFrom
        val ringingSince = config.ringingSinceMs
        val answeredAt = config.callAnsweredMs
        config.ringingFrom = ""
        config.ringingSinceMs = 0L
        config.callAnsweredMs = 0L
        if (from.isEmpty()) return

        val now = System.currentTimeMillis()
        val answered = answeredAt > 0L
        val event = JSONObject()
            .put("kind", "call")
            .put("address", if (from == UNKNOWN_CALLER) "" else from)
            .put("ts", if (ringingSince > 0L) ringingSince else now)
            .put("direction", if (answered) "in" else "missed")
            .put("duration", if (answered) (now - answeredAt) / 1000 else 0)

        Inbox.record(context, event)
        HostFiles.writeStatus(context)
    }

    private companion object {
        const val UNKNOWN_CALLER = "unknown"
    }
}
