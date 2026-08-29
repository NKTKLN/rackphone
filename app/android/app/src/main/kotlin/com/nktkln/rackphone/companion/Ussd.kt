package com.nktkln.rackphone.companion

import android.Manifest
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.telephony.TelephonyManager
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/**
 * USSD, which is how a prepaid SIM answers "how much is left".
 *
 * Same reason as sending: there is no shell path. `service call phone <txn>`
 * needs a transaction number that moves between Android versions, and a wrong
 * guess dials something. `TelephonyManager.sendUssdRequest` is the supported
 * route and it needs an app holding `CALL_PHONE`.
 *
 * The answer arrives on a callback, seconds later, which is why the caller
 * passes a continuation rather than getting a return value: the receiver holds
 * the broadcast open until it lands, so a root shell gets the operator's reply
 * in the `am broadcast` result instead of polling a file for it.
 */
object Ussd {

    /**
     * How long to wait for the network. Operators answer in two to five
     * seconds; past fifteen the request is lost, not slow, and a receiver that
     * kept waiting would be killed by the system anyway.
     */
    const val TIMEOUT_MS = 15_000L

    /**
     * Run a USSD code on one subscription.
     *
     * @param code The code to dial, `*102#` and the like.
     * @param subId Which SIM asks; `-1` lets Android choose.
     * @param onResult Called exactly once, with the outcome.
     */
    fun request(
        context: Context,
        code: String,
        subId: Int,
        onResult: (JSONObject) -> Unit,
    ) {
        val settled = AtomicBoolean(false)
        fun finish(result: JSONObject) {
            if (settled.compareAndSet(false, true)) {
                result.put("code", code).put("sub_id", subId)
                onResult(result)
            }
        }

        if (!HostFiles.granted(context, Manifest.permission.CALL_PHONE)) {
            return finish(failure("permission_denied"))
        }
        if (code.isBlank()) return finish(failure("empty_code"))

        val telephony = telephonyFor(context, subId)
            ?: return finish(failure("no_telephony"))

        val handler = Handler(Looper.getMainLooper())
        handler.postDelayed({ finish(failure("timeout")) }, TIMEOUT_MS)

        val callback = object : TelephonyManager.UssdResponseCallback() {
            override fun onReceiveUssdResponse(
                telephonyManager: TelephonyManager,
                request: String,
                response: CharSequence,
            ) {
                val text = response.toString()
                val result = JSONObject()
                    .put("status", "ok")
                    .put("response", text)
                    .put("ts", System.currentTimeMillis())
                parseAmount(text)?.let { result.put("amount", it) }
                if (subId >= 0) Config.of(context).recordBalance(subId, text, parseAmount(text))
                finish(result)
            }

            override fun onReceiveUssdResponseFailed(
                telephonyManager: TelephonyManager,
                request: String,
                failureCode: Int,
            ) {
                finish(failure(failureName(failureCode)))
            }
        }

        // The call itself can throw on a device that has the API but no
        // network stack behind it, and a receiver that dies here would leave
        // the broadcast open until the system kills it.
        runCatching { telephony.sendUssdRequest(code, callback, handler) }
            .onFailure { finish(failure("send_failed:" + it.javaClass.simpleName)) }
    }

    /**
     * Pull a money amount out of an operator's reply.
     *
     * Deliberately crude. Every operator writes this differently - "Balans:
     * 123,45 r", "Ваш баланс 1 234.5 руб" - and the only thing they agree on
     * is that the first number in the message is the one being reported. A
     * parser that tried to understand the sentence would be wrong in a new way
     * per operator; this one is wrong in the same way for all of them, and the
     * raw text is kept alongside so nothing is lost.
     */
    fun parseAmount(response: String): Double? {
        val match = AMOUNT.find(response) ?: return null
        return match.value
            .replace("\u00A0", "")
            .replace(" ", "")
            .replace(',', '.')
            .toDoubleOrNull()
    }

    /** A number, with either separator, and thousands grouped or not. */
    private val AMOUNT = Regex("-?\\d+(?:[\\u00A0 ]\\d{3})*(?:[.,]\\d+)?")

    private fun failure(reason: String): JSONObject =
        JSONObject().put("status", "failed").put("error", reason).put(
            "ts",
            System.currentTimeMillis(),
        )

    private fun failureName(code: Int): String = when (code) {
        TelephonyManager.USSD_RETURN_FAILURE -> "ussd_return_failure"
        TelephonyManager.USSD_ERROR_SERVICE_UNAVAIL -> "service_unavailable"
        else -> "error_$code"
    }

    private fun telephonyFor(context: Context, subId: Int): TelephonyManager? {
        val base = context.getSystemService(TelephonyManager::class.java) ?: return null
        if (subId < 0) return base
        return runCatching { base.createForSubscriptionId(subId) }.getOrDefault(base)
    }
}
