package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Where the radio reports back.
 *
 * Not exported: the only sender is a PendingIntent this app built with an
 * explicit component, so nothing else can forge an outcome.
 */
class SendResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(Sender.EXTRA_ID) ?: return
        Sender.onPartResult(context, id, resultCode)
    }
}
