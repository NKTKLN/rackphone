package com.nktkln.rackphone.companion

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.telephony.SmsManager
import org.json.JSONObject

/** One request to put a message on the radio. */
data class SendRequest(
    val to: String,
    val body: String,
    val subId: Int,
    val source: String,
    val id: String,
)

/**
 * Sending, and the bookkeeping that makes a send observable from the host.
 *
 * `SmsManager` is the whole reason this APK exists. There is no shell path to
 * it: `cmd phone` has no send subcommand, and the `isms` binder needs a raw
 * transaction number that moves between Android versions. An app holding
 * `SEND_SMS` is the supported route, so the app is kept to exactly that job.
 *
 * Message bodies are never written to disk. The outbox keeps the destination,
 * the length and the outcome, which is what an operator needs to answer "did it
 * go out", and nothing that would turn a lost phone into a leak.
 */
object Sender {
    const val EXTRA_ID = "id"
    const val EXTRA_PART = "part"

    /**
     * Refuse anything longer than ten parts. A message that size is not what
     * this unit is for, and the per-app rate limit makes it expensive to
     * discover that at the operator's end.
     */
    private const val MAX_PARTS = 10

    /**
     * Hand a message to the radio.
     *
     * Returns as soon as the radio has accepted the request; the outcome
     * arrives later through [SendResultReceiver]. The returned record is what
     * gets appended to the outbox, so the caller can report it verbatim.
     */
    fun send(context: Context, request: SendRequest): JSONObject {
        val config = Config.of(context)
        val now = System.currentTimeMillis()

        val to = Numbers.sanitise(request.to)
            ?: return reject(context, request, "invalid_destination")
        if (request.body.isEmpty()) return reject(context, request, "empty_body")
        if (!HostFiles.granted(context, Manifest.permission.SEND_SMS)) {
            return reject(context, request, "permission_denied")
        }
        if (!Sims.hasActiveSim(context)) return reject(context, request, "no_sim")

        // Resolve "let Android pick" to the subscription it would have picked,
        // so the outbox and the per-SIM keepalive clocks name a real SIM rather
        // than -1. Which SIM sent is the whole question on a dual-SIM unit.
        val requested = if (request.subId >= 0) request.subId else config.subId
        val subId = if (requested >= 0) requested else Sims.defaultSubId()
        val manager = smsManager(context, subId)
            ?: return reject(context, request, "no_sms_manager")

        val parts = runCatching { manager.divideMessage(request.body) }.getOrNull()
        if (parts.isNullOrEmpty()) return reject(context, request, "empty_body")
        if (parts.size > MAX_PARTS) return reject(context, request, "too_long")

        val intents = ArrayList<PendingIntent>(parts.size)
        for (index in parts.indices) {
            intents.add(resultIntent(context, config, request.id, index))
        }

        val pending = JSONObject()
            .put("id", request.id)
            .put("to", to)
            .put("source", request.source)
            .put("sub_id", subId)
            .put("parts_total", parts.size)
            .put("parts_ok", 0)
            .put("parts_failed", 0)
            .put("body_chars", request.body.length)
            .put("queued_ms", now)
            .put("errors", "")
        config.putPending(request.id, pending.toString())

        val accepted = runCatching {
            manager.sendMultipartTextMessage(to, null, parts, intents, null)
        }
        if (accepted.isFailure) {
            config.clearPending(request.id)
            val cause = accepted.exceptionOrNull()
            return reject(
                context,
                request,
                "send_failed:" + (cause?.javaClass?.simpleName ?: "unknown"),
            )
        }

        val record = JSONObject(pending.toString())
            .put("ts", now)
            .put("status", "queued")
        HostFiles.record(context, record)
        HostFiles.writeStatus(context)
        return record
    }

    /**
     * Fold one part's outcome into its send, and resolve the send once every
     * part has reported.
     *
     * A multipart message can half-succeed. That is recorded as a failure with
     * the part counts intact rather than averaged into a verdict, because on an
     * unattended unit "three of four parts arrived" is the fact worth having.
     */
    fun onPartResult(context: Context, id: String, resultCode: Int) {
        val config = Config.of(context)
        val raw = config.pending(id) ?: return
        val pending = runCatching { JSONObject(raw) }.getOrNull() ?: return

        val ok = resultCode == Activity.RESULT_OK
        if (ok) {
            pending.put("parts_ok", pending.optInt("parts_ok") + 1)
        } else {
            pending.put("parts_failed", pending.optInt("parts_failed") + 1)
            val seen = pending.optString("errors")
            val name = errorName(resultCode)
            if (!seen.split(",").contains(name)) {
                pending.put("errors", if (seen.isEmpty()) name else "$seen,$name")
            }
        }

        val settled = pending.optInt("parts_ok") + pending.optInt("parts_failed")
        if (settled < pending.optInt("parts_total")) {
            config.putPending(id, pending.toString())
            return
        }

        config.clearPending(id)
        val failed = pending.optInt("parts_failed") > 0
        val errors = pending.optString("errors")
        val now = System.currentTimeMillis()
        val record = pending
            .put("ts", now)
            .put("status", if (failed) "failed" else "ok")
            .put("error", if (errors.isEmpty()) JSONObject.NULL else errors)

        if (failed) {
            config.countSentFailed()
        } else {
            config.countSentOk()
            // Any successful send is activity on that SIM, whoever asked for
            // it, so it pushes that SIM's keepalive out by a full interval.
            config.recordSuccess(pending.optInt("sub_id", Config.SUB_DEFAULT), now)
            Keepalive.schedule(context)
        }
        HostFiles.record(context, record)
        HostFiles.writeStatus(context)
    }

    /** Record a request that never reached the radio. */
    fun reject(context: Context, request: SendRequest, reason: String): JSONObject {
        val config = Config.of(context)
        config.countRejected()
        val record = JSONObject()
            .put("ts", System.currentTimeMillis())
            .put("id", request.id)
            .put("to", Numbers.sanitise(request.to) ?: request.to)
            .put("source", request.source)
            .put("body_chars", request.body.length)
            .put("status", "rejected")
            .put("error", reason)
        HostFiles.record(context, record)
        HostFiles.writeStatus(context)
        return record
    }

    /** Turn a radio result code into something a log reader can act on. */
    fun errorName(code: Int): String = when (code) {
        Activity.RESULT_OK -> "ok"
        SmsManager.RESULT_ERROR_GENERIC_FAILURE -> "generic_failure"
        SmsManager.RESULT_ERROR_RADIO_OFF -> "radio_off"
        SmsManager.RESULT_ERROR_NULL_PDU -> "null_pdu"
        SmsManager.RESULT_ERROR_NO_SERVICE -> "no_service"
        SmsManager.RESULT_ERROR_LIMIT_EXCEEDED -> "limit_exceeded"
        SmsManager.RESULT_ERROR_FDN_CHECK_FAILURE -> "fdn_check_failure"
        SmsManager.RESULT_ERROR_SHORT_CODE_NOT_ALLOWED -> "short_code_not_allowed"
        SmsManager.RESULT_ERROR_SHORT_CODE_NEVER_ALLOWED -> "short_code_never_allowed"
        else -> "error_$code"
    }

    private fun resultIntent(
        context: Context,
        config: Config,
        id: String,
        part: Int,
    ): PendingIntent {
        val intent = Intent(context, SendResultReceiver::class.java)
            .putExtra(EXTRA_ID, id)
            .putExtra(EXTRA_PART, part)
        return PendingIntent.getBroadcast(
            context,
            config.nextRequestCode(),
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    private fun smsManager(context: Context, subId: Int): SmsManager? {
        val base =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                context.getSystemService(SmsManager::class.java)
            } else {
                @Suppress("DEPRECATION")
                SmsManager.getDefault()
            } ?: return null

        if (subId < 0) return base
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            runCatching { base.createForSubscriptionId(subId) }.getOrDefault(base)
        } else {
            @Suppress("DEPRECATION")
            runCatching { SmsManager.getSmsManagerForSubscriptionId(subId) }.getOrDefault(base)
        }
    }
}
