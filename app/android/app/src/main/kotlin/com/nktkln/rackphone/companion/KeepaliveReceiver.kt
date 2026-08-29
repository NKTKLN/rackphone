package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** The alarm. Decides whether a message is actually due, then rearms. */
class KeepaliveReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Keepalive.fire(context, force = false)
    }
}
