package com.nktkln.rackphone.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import org.json.JSONObject
import java.security.MessageDigest
import java.util.UUID

/**
 * The control surface a root shell drives.
 *
 * Exported, because the caller is `am broadcast` running as shell or root -
 * a different uid, which cannot reach a private receiver. Export means any app
 * on the device could deliver an intent here too, so every command carries a
 * shared token that lives in this app's private storage. Only root can read it,
 * which on this unit is exactly the set of callers allowed to send a message.
 *
 * The reply goes back through the ordered-broadcast result, so the shell that
 * sent the command reads the outcome from `am broadcast` output rather than
 * having to poll a file:
 *
 * ```sh
 * am broadcast --user 0 -n com.nktkln.rackphone.companion/.CommandReceiver \
 *   -a com.nktkln.rackphone.companion.SEND \
 *   --es token "$TOKEN" --es to '+79001234567' --es body 'hello'
 * ```
 */
class CommandReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return reply(false, "no_action")
        val config = Config.of(context)

        // Setup is the one command that runs unauthenticated, and only while
        // there is no token yet. It hands out no secret: the token is written
        // to private storage, where the caller has to be root to read it.
        if (action == Commands.ACTION_SETUP && !config.hasToken) {
            val token = config.rotateToken()
            HostFiles.publishToken(context, token)
            Keepalive.schedule(context)
            return reply(true, JSONObject().put("token_set", true).put("rotated", true))
        }

        if (!authenticate(config, intent)) {
            config.countRejected()
            return reply(false, "bad_token")
        }

        when (action) {
            Commands.ACTION_SETUP -> {
                val fresh = string(intent, Commands.EXTRA_NEW_TOKEN)
                val token = if (fresh.isNullOrEmpty()) config.rotateToken() else {
                    config.token = fresh
                    fresh
                }
                HostFiles.publishToken(context, token)
                reply(true, JSONObject().put("token_set", true).put("rotated", true))
            }

            Commands.ACTION_SEND -> {
                val request = SendRequest(
                    to = string(intent, Commands.EXTRA_TO).orEmpty(),
                    body = string(intent, Commands.EXTRA_BODY).orEmpty(),
                    subId = int(intent, Commands.EXTRA_SUB) ?: Config.SUB_DEFAULT,
                    source = Commands.SOURCE_HOST,
                    // The host may supply its own id so a retried command is
                    // recognisable in the outbox as the same request.
                    id = string(intent, Commands.EXTRA_ID)?.takeIf { it.isNotBlank() }
                        ?: newId(),
                )
                val record = Sender.send(context, request)
                reply(record.optString("status") != "rejected", record)
            }

            Commands.ACTION_CONFIG -> {
                applyConfig(config, intent)
                Keepalive.schedule(context)
                Balance.schedule(context)
                reply(true, HostFiles.writeStatus(context))
            }

            Commands.ACTION_KEEPALIVE -> {
                val force = bool(intent, "force") ?: true
                reply(true, Keepalive.fire(context, force = force))
            }

            Commands.ACTION_STATUS -> reply(true, HostFiles.writeStatus(context))

            // Drain rotates the spool and reports how much is now in flight;
            // the batch itself is read from the file. A binder transaction is
            // the wrong place to carry two thousand messages, and the host is
            // root on this device anyway.
            Commands.ACTION_DRAIN -> {
                Inbox.drain(context)
                reply(
                    true,
                    JSONObject()
                        .put("status", "drained")
                        .put("pending", Inbox.pending(context))
                        .put("file", HostFiles.inflightPath(context)),
                )
            }

            Commands.ACTION_ACK -> {
                Inbox.ack(context)
                reply(true, JSONObject().put("status", "acked"))
            }

            Commands.ACTION_PEEK -> reply(
                true,
                JSONObject().put("pending", Inbox.pending(context)),
            )

            Commands.ACTION_RESET -> {
                Inbox.reset(context)
                reply(true, JSONObject().put("status", "reset"))
            }

            // USSD answers seconds later, on a callback. goAsync holds the
            // broadcast open until it does, so the shell that asked reads the
            // operator's reply in the `am broadcast` result rather than
            // polling a file for it - at the cost of a receiver that is alive
            // for as long as the network takes.
            Commands.ACTION_USSD -> {
                val code = string(intent, Commands.EXTRA_CODE).orEmpty()
                val sub = int(intent, Commands.EXTRA_SUB) ?: config.subId
                val pending = goAsync()
                Ussd.request(context, code, sub) { result ->
                    pending.resultCode =
                        if (result.optString("status") == "ok") RESULT_OK else RESULT_ERROR
                    pending.resultData = result.toString()
                    HostFiles.writeStatus(context)
                    pending.finish()
                }
            }

            Commands.ACTION_BALANCE -> {
                val pending = goAsync()
                Balance.refresh(context, force = bool(intent, "force") ?: true) { results ->
                    pending.resultCode = if (results.length() > 0) RESULT_OK else RESULT_ERROR
                    pending.resultData =
                        JSONObject().put("checked", results.length())
                            .put("results", results).toString()
                    pending.finish()
                }
            }

            else -> reply(false, "unknown_action")
        }
    }

    /**
     * Apply only the keys the caller actually sent.
     *
     * A CONFIG command is a patch, not a replacement, so the host can change
     * one setting without having to know - and restate - all the others.
     */
    private fun applyConfig(config: Config, intent: Intent) {
        bool(intent, Commands.EXTRA_KEEPALIVE_ENABLED)?.let { config.keepaliveEnabled = it }
        string(intent, Commands.EXTRA_KEEPALIVE_TO)?.let { config.keepaliveTo = it.trim() }
        int(intent, Commands.EXTRA_KEEPALIVE_INTERVAL_HOURS)?.let {
            config.keepaliveIntervalHours = it
        }
        string(intent, Commands.EXTRA_KEEPALIVE_BODY)?.let { config.keepaliveBody = it }
        string(intent, Commands.EXTRA_KEEPALIVE_SUBS)?.let { config.keepaliveSubs = it }
        int(intent, Commands.EXTRA_SUB)?.let { config.subId = it }
        bool(intent, Commands.EXTRA_COLLECT_SMS)?.let { config.collectSms = it }
        bool(intent, Commands.EXTRA_COLLECT_CALLS)?.let { config.collectCalls = it }
        bool(intent, Commands.EXTRA_INCLUDE_BODY)?.let { config.includeBody = it }
        int(intent, Commands.EXTRA_INBOX_CAP)?.let { config.inboxCap = it }
        string(intent, Commands.EXTRA_BALANCE_CODE)?.let { config.balanceCode = it }
        int(intent, Commands.EXTRA_BALANCE_INTERVAL_HOURS)?.let {
            config.balanceIntervalHours = it
        }
    }

    /**
     * Compare tokens in constant time.
     *
     * A length-or-first-difference comparison on a secret that an on-device
     * caller can retry without limit is worth avoiding, and the fix is one
     * call.
     */
    private fun authenticate(config: Config, intent: Intent): Boolean {
        if (!config.hasToken) return false
        val supplied = string(intent, Commands.EXTRA_TOKEN) ?: return false
        return MessageDigest.isEqual(
            supplied.toByteArray(Charsets.UTF_8),
            config.token.toByteArray(Charsets.UTF_8),
        )
    }

    private fun reply(ok: Boolean, payload: Any) {
        val data = when (payload) {
            is JSONObject -> payload.toString()
            else -> JSONObject().put("error", payload.toString()).toString()
        }
        resultCode = if (ok) RESULT_OK else RESULT_ERROR
        resultData = data
    }

    companion object {
        const val RESULT_OK = 0
        const val RESULT_ERROR = 1

        fun newId(): String = UUID.randomUUID().toString().take(12)

        /**
         * Read an extra as a string.
         *
         * `am broadcast --es` delivers strings, but a host that builds the
         * intent some other way may deliver a real int or boolean, and being
         * strict about that would only produce puzzling `invalid_destination`
         * errors. Absent stays absent - that is what makes CONFIG a patch.
         */
        @Suppress("DEPRECATION")
        fun string(intent: Intent, key: String): String? {
            val extras: Bundle = intent.extras ?: return null
            if (!extras.containsKey(key)) return null
            return extras.get(key)?.toString()
        }

        fun int(intent: Intent, key: String): Int? = string(intent, key)?.trim()?.toIntOrNull()

        fun bool(intent: Intent, key: String): Boolean? =
            when (string(intent, key)?.trim()?.lowercase()) {
                null -> null
                "1", "true", "yes", "on" -> true
                "0", "false", "no", "off" -> false
                else -> null
            }
    }
}
