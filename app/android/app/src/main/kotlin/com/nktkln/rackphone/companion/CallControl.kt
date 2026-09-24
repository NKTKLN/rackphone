// Every permission-gated call here runs inside runCatching, and a refused grant is reported as
// the outcome rather than thrown.
@file:SuppressLint("MissingPermission")

package com.nktkln.rackphone.companion

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.telecom.Call
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.telecom.VideoProfile
import android.telephony.TelephonyManager
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

/** DTMF can carry only the keys on a phone's keypad. */
fun isDtmfDigits(digits: String): Boolean =
    digits.isNotEmpty() && digits.length <= MAX_DTMF_DIGITS && digits.all { it in DTMF_KEYS }

private const val DTMF_KEYS = "0123456789*#"
private const val MAX_DTMF_DIGITS = 32

/**
 * Everything the host can do to a call.
 *
 * With this app as the default dialer, [ActiveCall] holds the call itself and
 * every operation goes through it. Without the role, answering and rejecting
 * fall back to TelecomManager, which is how they worked before the role
 * existed; dialling needs no role, while ending a call the unit placed and
 * sending tones need the [Call] and say so when it is missing.
 */
object CallControl {
    /** Gap between tones, long enough for an IVR to hear two presses. */
    private const val TONE_MS = 150L
    private const val GAP_MS = 100L

    /** When the last queued tone ends, on the uptime clock; main thread only. */
    private var dtmfFreeAt = 0L

    @Suppress("DEPRECATION")
    fun answer(context: Context): JSONObject {
        val call = ActiveCall.current
        if (call != null && call.state == Call.STATE_RINGING) {
            call.answer(VideoProfile.STATE_AUDIO_ONLY)
            return result(CallOperation.ANSWER, ringing = true, completed = true)
        }
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
        val call = ActiveCall.current
        if (call != null && call.state == Call.STATE_RINGING) {
            call.reject(false, null)
            return result(CallOperation.REJECT, ringing = true, completed = true)
        }
        val ringing = isRinging(context)
        if (!ringing) return result(CallOperation.REJECT, false, false)
        return result(CallOperation.REJECT, ringing, telecomEndCall(context))
    }

    /** Place a call; Telecom hands it to [RackInCallService] once it starts. */
    fun dial(context: Context, to: String): JSONObject {
        val number = Numbers.sanitise(to) ?: return status("rejected", "invalid_destination")
        if (ActiveCall.current != null || isRinging(context)) return status("rejected", "busy")
        if (!HostFiles.granted(context, Manifest.permission.CALL_PHONE)) {
            return status("rejected", "permission_denied")
        }
        val account = phoneAccountFor(context)
        val extras = Bundle().apply {
            if (account != null) putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, account)
        }
        val placed = runCatching {
            context.getSystemService(TelecomManager::class.java)
                .placeCall(Uri.fromParts("tel", number, null), extras)
            true
        }.getOrDefault(false)
        // The unit's own clock, so the host can tell this call's hang-up record
        // from a late one left by the call before it.
        return if (placed) {
            status("dialing").put("to", number).put("placed_at", System.currentTimeMillis())
        } else {
            status("failed")
        }
    }

    /**
     * The SIM a placed call goes out on, when there is a choice to make.
     *
     * With two SIMs and no default for calls, Telecom stops and asks on the
     * unit's screen, which nobody is looking at, so the call just hangs. The
     * configured SIM wins, then the one Android sends SMS from - the same rule
     * a send follows, so one unit speaks with one number.
     */
    @SuppressLint("MissingPermission")
    private fun phoneAccountFor(context: Context): PhoneAccountHandle? {
        val telecom = context.getSystemService(TelecomManager::class.java)
        val accounts = runCatching { telecom.callCapablePhoneAccounts }.getOrNull().orEmpty()
        if (accounts.size <= 1) return null
        val configured = Config.of(context).subId
        val wanted = if (configured >= 0) configured else Sims.defaultSubId()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val telephony = context.getSystemService(TelephonyManager::class.java)
            accounts.firstOrNull {
                runCatching { telephony.getSubscriptionId(it) }.getOrNull() == wanted
            }?.let { return it }
        }
        return accounts.first()
    }

    /** Hang up whatever call the unit has, ringing or not. */
    @Suppress("DEPRECATION")
    fun end(context: Context): JSONObject {
        val call = ActiveCall.current
        if (call != null) {
            if (call.state == Call.STATE_RINGING) call.reject(false, null) else call.disconnect()
            return status("ended")
        }
        // Without the role there is no Call, but TelecomManager can still end
        // the one ongoing call, which on this unit is the only one there is.
        return if (telecomEndCall(context)) status("ended") else status("no_call")
    }

    /** Press keys on the call's keypad, one after another. */
    @Suppress("DEPRECATION")
    fun dtmf(digits: String): JSONObject {
        if (!isDtmfDigits(digits)) return status("rejected", "invalid_digits")
        val call = ActiveCall.current ?: return status("no_call")
        if (call.state != Call.STATE_ACTIVE) return status("no_call")
        // Tones are timed on the main looper rather than slept through: the
        // broadcast that asked has already been answered by then. Each
        // request queues behind the tones still to play, so quick presses
        // come out whole and in the order they arrived.
        val handler = Handler(Looper.getMainLooper())
        val start = maxOf(SystemClock.uptimeMillis(), dtmfFreeAt)
        digits.forEachIndexed { index, digit ->
            val at = start + index * (TONE_MS + GAP_MS)
            handler.postAtTime({ call.playDtmfTone(digit) }, at)
            handler.postAtTime({ call.stopDtmfTone() }, at + TONE_MS)
        }
        dtmfFreeAt = start + digits.length * (TONE_MS + GAP_MS)
        return status("sent").put("digits", digits.length)
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

    /** End the ongoing call without a [Call]; TelecomManager can from API 28. */
    @Suppress("DEPRECATION")
    private fun telecomEndCall(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return false
        return runCatching {
            context.getSystemService(TelecomManager::class.java).endCall()
        }.getOrDefault(false)
    }

    private fun result(
        operation: CallOperation,
        ringing: Boolean,
        completed: Boolean,
    ): JSONObject = JSONObject().put("status", callOutcome(operation, ringing, completed))

    private fun status(status: String, error: String? = null): JSONObject =
        JSONObject().put("status", status).apply { if (error != null) put("error", error) }
}
