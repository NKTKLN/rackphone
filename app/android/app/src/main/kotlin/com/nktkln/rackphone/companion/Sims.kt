// Nothing here throws: a missing READ_PHONE_STATE is checked through HostFiles.granted or
// absorbed by runCatching, and reported as absent.
@file:SuppressLint("MissingPermission")

package com.nktkln.rackphone.companion

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.telecom.PhoneAccountHandle
import android.telephony.SubscriptionInfo
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import org.json.JSONArray
import org.json.JSONObject

/**
 * What the modem can tell us about the SIMs.
 *
 * Every call here is best-effort. Reading the subscription list needs
 * `READ_PHONE_STATE`, which is a convenience rather than a requirement: a unit
 * with one SIM and an explicit `keepalive_to` sends fine without it. So nothing
 * in this file throws - an unavailable answer is reported as absent.
 */
object Sims {

    /** Describe every active subscription, for the UI and for `status.json`. */
    fun list(context: Context): JSONArray {
        val out = JSONArray()
        val manager = subscriptions(context) ?: return out
        val infos: List<SubscriptionInfo> =
            runCatching { manager.activeSubscriptionInfoList }.getOrNull().orEmpty()
        for (info in infos) {
            out.put(
                JSONObject()
                    .put("sub_id", info.subscriptionId)
                    .put("slot", info.simSlotIndex)
                    .put("carrier", info.carrierName?.toString() ?: "")
                    .put("label", info.displayName?.toString() ?: "")
                    .put("is_default_sms", info.subscriptionId == defaultSubId())
                    .put("number", numberOf(context, manager, info) ?: "")
            )
        }
        return out
    }

    /** The subscription ids the modem reports, empty when unreadable. */
    fun activeSubIds(context: Context): List<Int> {
        val manager = subscriptions(context) ?: return emptyList()
        return runCatching { manager.activeSubscriptionInfoList }
            .getOrNull()
            .orEmpty()
            .map { it.subscriptionId }
    }

    /**
     * Whether a SIM is present and unlocked.
     *
     * Uses the SIM state rather than the subscription list because this answer
     * is part of "can this unit send at all", which must stay truthful on a
     * device where `READ_PHONE_STATE` was never granted.
     */
    fun hasActiveSim(context: Context): Boolean {
        val telephony = context.getSystemService(TelephonyManager::class.java) ?: return false
        return runCatching { telephony.simState == TelephonyManager.SIM_STATE_READY }
            .getOrDefault(false)
    }

    /**
     * Whether [sub] names a SIM this unit has, with [Config.SUB_DEFAULT]
     * always allowed. An unreadable list says nothing either way, so it lets
     * the request through for the radio to judge rather than refusing every
     * explicit choice on a unit without `READ_PHONE_STATE`.
     */
    fun isKnown(context: Context, sub: Int): Boolean {
        if (sub < 0) return true
        val known = activeSubIds(context)
        return known.isEmpty() || sub in known
    }

    /**
     * The SIM behind a call's phone account, or [Config.SUB_DEFAULT] when
     * there is no account or this Android cannot map one (before 11).
     */
    fun subIdOf(context: Context, account: PhoneAccountHandle?): Int {
        if (account == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return Config.SUB_DEFAULT
        }
        val telephony = context.getSystemService(TelephonyManager::class.java)
            ?: return Config.SUB_DEFAULT
        return runCatching { telephony.getSubscriptionId(account) }
            .getOrNull()
            ?.takeIf { it != SubscriptionManager.INVALID_SUBSCRIPTION_ID }
            ?: Config.SUB_DEFAULT
    }

    /** The subscription Android would use for an unqualified send. */
    fun defaultSubId(): Int =
        runCatching { SubscriptionManager.getDefaultSmsSubscriptionId() }
            .getOrDefault(Config.SUB_DEFAULT)

    /**
     * This unit's own number, for `keepalive_to=self`.
     *
     * Often blank: the number lives on the SIM only if the operator wrote it
     * there, and many do not. When it is missing the keepalive says so in
     * `status.json` instead of silently sending nothing, because "my SIM is
     * being kept alive" is precisely the belief that must not be wrong.
     */
    fun selfNumber(context: Context, subId: Int): String? {
        val manager = subscriptions(context) ?: return null
        val wanted = if (subId >= 0) subId else defaultSubId()
        val infos: List<SubscriptionInfo> =
            runCatching { manager.activeSubscriptionInfoList }.getOrNull().orEmpty()
        val info = infos.firstOrNull { it.subscriptionId == wanted } ?: infos.firstOrNull()
        return info?.let { Numbers.sanitise(numberOf(context, manager, it)) }
    }

    private fun numberOf(
        context: Context,
        manager: SubscriptionManager,
        info: SubscriptionInfo,
    ): String? {
        if (!HostFiles.granted(context, Manifest.permission.READ_PHONE_STATE)) return null
        val raw =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                runCatching { manager.getPhoneNumber(info.subscriptionId) }.getOrNull()
            } else {
                @Suppress("DEPRECATION")
                runCatching { info.number }.getOrNull()
            }
        return raw?.takeIf { it.isNotBlank() }
    }

    private fun subscriptions(context: Context): SubscriptionManager? =
        context.getSystemService(SubscriptionManager::class.java)
}
