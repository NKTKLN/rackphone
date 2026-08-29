package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * The balance alarm. Holds the broadcast open while the operator answers,
 * because the process would otherwise be gone before the USSD callback fires.
 */
class BalanceReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        Balance.refresh(context, force = false) { pending.finish() }
    }
}
