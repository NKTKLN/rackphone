package com.nktkln.rackphone.companion

import android.app.Activity
import android.os.Bundle

/**
 * The dial screen Android insists a default dialer has, and nothing more.
 *
 * The dialer role is granted only to an app that answers `ACTION_DIAL`. This
 * unit is dialled from the client, never from its own screen, so the activity
 * closes as soon as it opens; the role is what matters, for the [Call] objects
 * it brings with it.
 */
class DialActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        finish()
    }
}
