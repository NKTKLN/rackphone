package com.nktkln.rackphone.companion

/**
 * The control surface, in one place.
 *
 * A root shell on the unit drives this app entirely through broadcasts, so
 * these strings are a published interface: the Magisk plugin and the host CLI
 * both spell them out. Changing one is a breaking change for the unit.
 */
object Commands {
    const val PKG = "com.nktkln.rackphone.companion"

    const val ACTION_SEND = "$PKG.SEND"
    const val ACTION_CONFIG = "$PKG.CONFIG"
    const val ACTION_KEEPALIVE = "$PKG.KEEPALIVE"
    const val ACTION_STATUS = "$PKG.STATUS"
    const val ACTION_SETUP = "$PKG.SETUP"

    // The delivery contract with the host, in the same shape the shell
    // collector used: rotate, read, confirm.
    const val ACTION_DRAIN = "$PKG.DRAIN"
    const val ACTION_ACK = "$PKG.ACK"
    const val ACTION_PEEK = "$PKG.PEEK"
    const val ACTION_RESET = "$PKG.RESET"

    /** Ask the network something. `BALANCE` is the scheduled special case. */
    const val ACTION_USSD = "$PKG.USSD"
    const val ACTION_BALANCE = "$PKG.BALANCE"

    const val EXTRA_TOKEN = "token"
    const val EXTRA_NEW_TOKEN = "new_token"
    const val EXTRA_TO = "to"
    const val EXTRA_BODY = "body"
    const val EXTRA_ID = "id"
    const val EXTRA_SUB = "sub"

    const val EXTRA_KEEPALIVE_ENABLED = "keepalive_enabled"
    const val EXTRA_KEEPALIVE_TO = "keepalive_to"
    const val EXTRA_KEEPALIVE_INTERVAL_HOURS = "keepalive_interval_hours"
    const val EXTRA_KEEPALIVE_BODY = "keepalive_body"
    const val EXTRA_KEEPALIVE_SUBS = "keepalive_subs"

    const val EXTRA_COLLECT_SMS = "collect_sms"
    const val EXTRA_COLLECT_CALLS = "collect_calls"
    const val EXTRA_INCLUDE_BODY = "include_body"
    const val EXTRA_INBOX_CAP = "inbox_cap"
    const val EXTRA_CODE = "code"
    const val EXTRA_BALANCE_CODE = "balance_code"
    const val EXTRA_BALANCE_INTERVAL_HOURS = "balance_interval_hours"

    /** Who asked for a send. Kept on every journal record. */
    const val SOURCE_HOST = "host"
    const val SOURCE_KEEPALIVE = "keepalive"
    const val SOURCE_UI = "ui"
}
