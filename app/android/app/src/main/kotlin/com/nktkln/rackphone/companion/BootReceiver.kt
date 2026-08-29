package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Alarms do not survive a reboot, and this unit reboots after every module
 * update. Without this the keepalive would silently stop at the first restart -
 * the failure mode the whole feature exists to prevent.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Keepalive.schedule(context)
        Balance.schedule(context)
        HostFiles.writeStatus(context)
    }
}
