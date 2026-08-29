package com.nktkln.rackphone.companion

import android.Manifest
import android.content.Context
import android.os.Build
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
