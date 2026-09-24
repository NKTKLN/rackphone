package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** Plain call states kept outside Android so their event decisions stay testable. */
enum class CallState { IDLE, RINGING, OFFHOOK }

/** A ringing event belongs only to the transition into ringing, not its repeats. */
fun shouldRecordRinging(wasRinging: Boolean, state: CallState): Boolean =
    !wasRinging && state == CallState.RINGING

/**
 * Whether a repeat of ringing finally names the caller.
 *
 * Android sends PHONE_STATE twice for one ringing call: once to everyone,
 * without the number, and once to holders of READ_CALL_LOG, with it. They can
 * arrive in either order, so the first one seen may be the blank one.
 */
fun namesCallerLate(wasRinging: Boolean, recorded: String, number: String?): Boolean =
    wasRinging && recorded == UNKNOWN_CALLER && !number.isNullOrEmpty()

const val UNKNOWN_CALLER = "unknown"

/** Preserve a new call's id until its outcome; an existing id always wins. */
fun correlatedCallId(existing: String, generated: String): String =
    existing.ifEmpty { generated }

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
        val wasRinging = config.ringingFrom.isNotEmpty()
        val from = Numbers.sanitise(number)
            ?: Numbers.sanitise(ActiveCall.ringingNumber().orEmpty())
            ?: UNKNOWN_CALLER
        val now = System.currentTimeMillis()
        if (namesCallerLate(wasRinging, config.ringingFrom, from.takeIf { it != UNKNOWN_CALLER })) {
            // The call was reported without a number; say who it is now, under
            // the same call id, so the client can put a name on the screen and
            // the outcome is logged against the right number.
            config.ringingFrom = from
            writeCurrentCall(context, from)
            recordRinging(context, from, config.ringingSinceMs, callId(context))
            return
        }
        if (!shouldRecordRinging(wasRinging, CallState.RINGING)) return

        val callId = correlatedCallId(callId(context), UUID.randomUUID().toString().take(12))
        persistCallId(context, callId)
        config.ringingFrom = from
        config.ringingSinceMs = now
        config.callAnsweredMs = 0L
        writeCurrentCall(context, from)
        recordRinging(context, from, now, callId)
    }

    private fun recordRinging(context: Context, from: String, since: Long, callId: String) {
        Inbox.record(
            context,
            JSONObject()
                .put("kind", "call")
                .put("address", if (from == UNKNOWN_CALLER) "" else from)
                .put("ts", since)
                .put("direction", "ringing")
                .put("call_id", callId),
        )
        HostFiles.writeStatus(context)
    }

    private fun finish(context: Context, config: Config) {
        val from = config.ringingFrom
        val ringingSince = config.ringingSinceMs
        val answeredAt = config.callAnsweredMs
        val callId = callId(context)
        config.ringingFrom = ""
        config.ringingSinceMs = 0L
        config.callAnsweredMs = 0L
        persistCallId(context, "")
        writeCurrentCall(context, "")
        if (from.isEmpty()) return

        val now = System.currentTimeMillis()
        val answered = answeredAt > 0L
        val event = JSONObject()
            .put("kind", "call")
            .put("address", if (from == UNKNOWN_CALLER) "" else from)
            .put("ts", if (ringingSince > 0L) ringingSince else now)
            .put("direction", if (answered) "in" else "missed")
            .put("duration", if (answered) (now - answeredAt) / 1000 else 0)
            .put("call_id", callId)

        Inbox.record(context, event)
        HostFiles.writeStatus(context)
    }

    private companion object {
        const val PREFS = "rackphone"
        const val KEY_CALL_ID = "ringing_call_id"
        const val CURRENT_CALL = "current-call.env"

        fun callId(context: Context): String =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_CALL_ID, "").orEmpty()

        /** Commit before spooling: the id must outlive a kill after this broadcast. */
        fun persistCallId(context: Context, callId: String) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY_CALL_ID, callId).commit()
        }

        /** Root-readable current state lets status answer without draining events. */
        fun writeCurrentCall(context: Context, from: String) {
            val file = File(HostFiles.dir(context), CURRENT_CALL)
            if (from.isEmpty()) file.delete()
            else file.writeText("ringing=true\nringing_from=$from\n")
        }
    }
}
