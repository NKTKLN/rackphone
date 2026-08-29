package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.telephony.SubscriptionManager
import org.json.JSONObject

/**
 * Incoming SMS, taken from the broadcast rather than from the database.
 *
 * The shell collector this replaces read `mmssms.db` with `sqlite3` as root,
 * because no supported shell command returns a message body unambiguously. An
 * app holding `RECEIVE_SMS` is handed the message directly, in parts, already
 * decoded - so the on-disk schema of a provider that a LineageOS upgrade could
 * move stops being part of this project's contract.
 *
 * The receiver is restricted to `BROADCAST_SMS`, which only the system holds,
 * so nothing on the device can fabricate an incoming message.
 */
class SmsReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val config = Config.of(context)
        if (!config.collectSms) return

        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (parts.isEmpty()) return

        // A long message arrives as several PDUs that are one message to the
        // reader; joining them here means the host never has to reassemble.
        val body = parts.joinToString("") { it.displayMessageBody.orEmpty() }
        val first = parts.first()

        val event = JSONObject()
            .put("kind", "sms")
            .put("address", first.displayOriginatingAddress.orEmpty())
            .put("ts", first.timestampMillis)
            .put("direction", "in")
            .put(
                "sub",
                intent.getIntExtra(
                    SubscriptionManager.EXTRA_SUBSCRIPTION_INDEX,
                    Config.SUB_DEFAULT,
                ),
            )
            // include_body=0: the host learns that a message arrived, from whom
            // and when, and the text never leaves the phone.
            .put("body", if (config.includeBody) body else JSONObject.NULL)

        Inbox.record(context, event)
        HostFiles.writeStatus(context)
    }
}
