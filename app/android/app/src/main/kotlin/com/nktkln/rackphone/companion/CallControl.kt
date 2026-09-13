package com.nktkln.rackphone.companion

import android.content.Context
import android.telecom.TelecomManager
import org.json.JSONObject

enum class CallOperation { ANSWER, REJECT }

/** Map the observed operation result to the exact outcome reported to an operator. */
fun callOutcome(operation: CallOperation, ringing: Boolean, completed: Boolean): String =
    when {
        !ringing -> "no_ringing_call"
        !completed -> "failed"
        operation == CallOperation.ANSWER -> "answered"
        else -> "rejected"
    }

object CallControl {
    fun answer(context: Context): JSONObject {
        val ringing = isRinging(context)
        if (!ringing) return result(CallOperation.ANSWER, false, false)
        val completed = runCatching {
            context.getSystemService(TelecomManager::class.java).acceptRingingCall()
            true
        }.getOrDefault(false)
        return result(CallOperation.ANSWER, ringing, completed)
    }

    @Suppress("DEPRECATION")
    fun reject(context: Context): JSONObject {
        val ringing = isRinging(context)
        if (!ringing) return result(CallOperation.REJECT, false, false)
        val completed = runCatching {
            context.getSystemService(TelecomManager::class.java).endCall()
        }.getOrDefault(false)
        return result(CallOperation.REJECT, ringing, completed)
    }

    /**
     * Whether a call is ringing, according to this app's own state machine.
     *
     * `TelecomManager.isRinging` is hidden API, and `TelephonyManager.callState`
     * needs a permission to say anything useful. Neither is necessary:
     * `CallReceiver` already tracks ringing across broadcasts, because that is
     * what turns three phone states into the events this app relays. Asking it
     * keeps one answer to the question instead of two that can disagree.
     */
    private fun isRinging(context: Context): Boolean =
        Config.of(context).ringingFrom.isNotEmpty()

    private fun result(
        operation: CallOperation,
        ringing: Boolean,
        completed: Boolean,
    ): JSONObject = JSONObject().put("status", callOutcome(operation, ringing, completed))
}
