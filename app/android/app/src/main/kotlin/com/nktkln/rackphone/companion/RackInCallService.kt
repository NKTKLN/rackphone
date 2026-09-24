package com.nktkln.rackphone.companion

import android.os.Build
import android.telecom.Call
import android.telecom.InCallService
import org.json.JSONObject

/**
 * The unit's in-call service, bound by Telecom while this app is the default
 * dialer.
 *
 * Being bound is what hands over a [Call] object, and a [Call] is the only
 * supported way to send DTMF, end a call this app did not start, or answer
 * without the deprecated TelecomManager shortcuts. There is no screen here: the
 * unit is racked, and the person on the call is on the client.
 *
 * Incoming calls are still reported by [CallReceiver] from the phone state, as
 * before. What this adds to the log is the one thing the phone state cannot
 * tell apart: a call the unit placed.
 */
class RackInCallService : InCallService() {

    override fun onCallAdded(call: Call) {
        ActiveCall.attach(call)
        call.registerCallback(callback)
    }

    override fun onCallRemoved(call: Call) {
        call.unregisterCallback(callback)
        recordIfOutgoing(call)
        ActiveCall.detach(call)
    }

    private val callback = object : Call.Callback() {
        override fun onStateChanged(call: Call, state: Int) {
            if (state == Call.STATE_ACTIVE) ActiveCall.markActive(call)
        }
    }

    private fun recordIfOutgoing(call: Call) {
        // Detached right after, so this reads the call's last known shape.
        val tracked = ActiveCall.tracked(call) ?: return
        if (!tracked.outgoing || !Config.of(this).collectCalls) return
        val now = System.currentTimeMillis()
        val activeAt = tracked.activeSinceMs
        val number = call.details?.handle?.schemeSpecificPart.orEmpty()
        Inbox.record(
            this,
            JSONObject()
                .put("kind", "call")
                .put("address", Numbers.sanitise(number) ?: number)
                .put("ts", tracked.startedAtMs)
                .put("direction", "out")
                .put("duration", if (activeAt == null) 0 else (now - activeAt) / 1000),
        )
        HostFiles.writeStatus(this)
    }
}

/**
 * The calls the unit has, as the in-call service last saw them.
 *
 * Usually one, but a call waiting behind an active one is a second [Call], and
 * losing track of the first when the second is rejected would leave the host
 * unable to reach the call that is still going.
 *
 * A process-wide holder because the broadcast receiver that carries the host's
 * commands and the service that owns the calls are separate components of one
 * process; both run on the main thread, so a plain map is enough.
 */
object ActiveCall {
    class Tracked(val outgoing: Boolean, val startedAtMs: Long, var activeSinceMs: Long?)

    private val calls = LinkedHashMap<Call, Tracked>()

    /** The call the host's commands act on: the one in progress, else the newest. */
    @Suppress("DEPRECATION")
    val current: Call?
        get() = calls.keys.firstOrNull { it.state == Call.STATE_ACTIVE } ?: calls.keys.lastOrNull()

    // Call.state rather than Call.Details.state: the latter is API 31, and this
    // app still runs on 26.
    @Suppress("DEPRECATION")
    fun attach(added: Call) {
        val now = System.currentTimeMillis()
        calls[added] = Tracked(
            outgoing = directionIsOutgoing(added),
            startedAtMs = now,
            activeSinceMs = if (added.state == Call.STATE_ACTIVE) now else null,
        )
    }

    fun markActive(changed: Call) {
        val tracked = calls[changed] ?: return
        if (tracked.activeSinceMs == null) tracked.activeSinceMs = System.currentTimeMillis()
    }

    fun tracked(of: Call): Tracked? = calls[of]

    fun detach(removed: Call) {
        calls.remove(removed)
    }

    /** Whether the unit placed this call, rather than answered it. */
    @Suppress("DEPRECATION")
    private fun directionIsOutgoing(added: Call): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            added.details?.callDirection == Call.Details.DIRECTION_OUTGOING
        } else {
            // Before Q a new call is either ringing, which is incoming, or on
            // its way out.
            added.state != Call.STATE_RINGING
        }
}
