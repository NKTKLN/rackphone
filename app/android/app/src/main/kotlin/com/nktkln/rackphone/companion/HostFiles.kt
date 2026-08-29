package com.nktkln.rackphone.companion

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * The files the host reads.
 *
 * This app has no socket and no push. It writes three files into its private
 * `files/rackphone/` directory and a root shell reads them over adb, which is
 * the same shape the rest of Rackphone already uses: the phone produces, the
 * host collects. Private storage rather than `/sdcard` keeps destinations
 * unreadable by anything on the device that is not root.
 *
 *  - `token`        the shared secret a broadcast must carry
 *  - `status.json`  the current state, rewritten after anything changes
 *  - `outbox.jsonl` an append-only record of every send attempt
 */
object HostFiles {
    private const val DIR = "rackphone"
    private const val TOKEN = "token"
    private const val STATUS = "status.json"
    private const val STATUS_ENV = "status.env"
    private const val OUTBOX = "outbox.jsonl"

    /**
     * How much of the outbox is kept. The host is expected to collect it; this
     * only bounds a unit whose host has stopped asking.
     */
    private const val OUTBOX_MAX_LINES = 200

    fun dir(context: Context): File =
        File(context.filesDir, DIR).apply { if (!exists()) mkdirs() }

    fun tokenFile(context: Context): File = File(dir(context), TOKEN)

    fun statusFile(context: Context): File = File(dir(context), STATUS)

    /**
     * The same state as `key=value`, for the Magisk plugin.
     *
     * The plugin's scripts are POSIX sh with no `jq` on the device, and a
     * status document parsed with `sed` would be a parser nobody tested. The
     * app already knows the values; writing them twice costs one file and
     * removes the parsing entirely.
     */
    fun statusEnvFile(context: Context): File = File(dir(context), STATUS_ENV)

    fun outboxFile(context: Context): File = File(dir(context), OUTBOX)

    /** Where the host reads a drained batch from. */
    fun inflightPath(context: Context): String =
        File(dir(context), "inbox.inflight").absolutePath

    /** Mirror the token to disk so a root shell can read it once, at setup. */
    fun publishToken(context: Context, token: String) {
        writeAtomic(tokenFile(context), token + "\n")
    }

    /**
     * Append one attempt to the outbox.
     *
     * Records are appended, never rewritten, so a send appears twice: once as
     * `queued` when the radio accepted it and once resolved. The host takes the
     * last record per `id`. An entry that never resolves is a real observation -
     * the radio took the message and said nothing - and hiding it by updating a
     * row in place would lose exactly the case worth seeing.
     */
    fun record(context: Context, entry: JSONObject) {
        val file = outboxFile(context)
        runCatching { file.appendText(entry.toString() + "\n") }
        trim(file)
    }

    /** Read the tail of the outbox, newest first. */
    fun recent(context: Context, limit: Int): JSONArray {
        val file = outboxFile(context)
        if (!file.exists()) return JSONArray()
        val lines = runCatching { file.readLines() }.getOrDefault(emptyList())
        val out = JSONArray()
        for (line in lines.asReversed()) {
            if (out.length() >= limit) break
            if (line.isBlank()) continue
            runCatching { out.put(JSONObject(line)) }
        }
        return out
    }

    /**
     * Rewrite `status.json`.
     *
     * Called after every command rather than on a timer: the host reads this
     * file immediately after the broadcast it just sent, so it has to be true
     * by the time the receiver returns.
     */
    fun writeStatus(context: Context): JSONObject {
        val status = buildStatus(context)
        writeAtomic(statusFile(context), status.toString(2) + "\n")
        writeAtomic(statusEnvFile(context), flatten(status))
        return status
    }

    /**
     * Flatten the parts of the status a shell script asks about.
     *
     * Deliberately not every field: this is the plugin's status and metrics
     * surface, and a key here is one the host can be shown or alerted on.
     */
    private fun flatten(status: JSONObject): String {
        val keepalive = status.optJSONObject("keepalive") ?: JSONObject()
        val inbox = status.optJSONObject("inbox") ?: JSONObject()
        val counters = status.optJSONObject("counters") ?: JSONObject()
        val targets = keepalive.optJSONArray("targets") ?: JSONArray()

        var unresolved = 0
        for (index in 0 until targets.length()) {
            if (!targets.optJSONObject(index).optBoolean("resolves")) unresolved++
        }

        val balances = status.optJSONArray("balances") ?: JSONArray()

        return buildString {
            appendLine("ready=" + status.optBoolean("ready"))
            appendLine("token_set=" + status.optBoolean("token_set"))
            appendLine("generated_ms=" + status.optLong("generated_ms"))
            appendLine("sims=" + (status.optJSONArray("sims")?.length() ?: 0))
            val power = status.optJSONObject("power") ?: JSONObject()
            appendLine("battery_exempt=" + power.optBoolean("battery_exempt"))
            appendLine("standby_bucket=" + power.optString("standby_bucket"))
            appendLine("collect_sms=" + inbox.optBoolean("collect_sms"))
            appendLine("collect_calls=" + inbox.optBoolean("collect_calls"))
            appendLine("include_body=" + inbox.optBoolean("include_body"))
            appendLine("pending=" + inbox.optInt("pending"))
            appendLine("dropped=" + inbox.optInt("dropped"))
            appendLine("keepalive_enabled=" + keepalive.optBoolean("enabled"))
            appendLine("keepalive_subs=" + keepalive.optString("subs"))
            appendLine("keepalive_interval_hours=" + keepalive.optInt("interval_hours"))
            appendLine("keepalive_targets=" + targets.length())
            appendLine("keepalive_unresolved=" + unresolved)
            appendLine("keepalive_next_due_ms=" + keepalive.optLong("next_due_ms", 0L))
            appendLine("sent_ok=" + counters.optInt("sent_ok"))
            appendLine("sent_failed=" + counters.optInt("sent_failed"))
            appendLine("rejected=" + counters.optInt("rejected"))
            appendLine("in_flight=" + counters.optInt("pending"))
            // One line per SIM that has ever answered, so a shell can export a
            // gauge without parsing an array.
            for (index in 0 until balances.length()) {
                val entry = balances.optJSONObject(index) ?: continue
                val sub = entry.optInt("sub_id")
                if (entry.has("amount")) {
                    appendLine("balance_sub$sub=" + entry.optDouble("amount"))
                }
                appendLine("balance_checked_ms_sub$sub=" + entry.optLong("checked_ms"))
            }
        }
    }

    /** Assemble the status document without writing it. */
    fun buildStatus(context: Context): JSONObject {
        val config = Config.of(context)
        val now = System.currentTimeMillis()

        // One entry per subscription being kept alive. A dual-SIM unit has two
        // independent clocks, and a single aggregate line would let the SIM
        // that is quietly dying hide behind the one the host uses daily.
        val targets = JSONArray()
        var earliest: Long? = null
        for (sub in Keepalive.subs(context)) {
            val target = Keepalive.targetFor(context, sub)
            val runAt = Keepalive.runAtFor(config, sub, now)
            if (earliest == null || runAt < earliest) earliest = runAt
            targets.put(
                JSONObject()
                    .put("sub_id", sub)
                    .put("to", target ?: "")
                    .put("resolves", target != null)
                    .put("last_success_ms", config.lastSuccessMs(sub))
                    .put("next_due_ms", runAt)
            )
        }

        val keepalive = JSONObject()
            .put("enabled", config.keepaliveEnabled)
            .put("to", config.keepaliveTo)
            .put("subs", config.keepaliveSubs)
            .put("interval_hours", config.keepaliveIntervalHours)
            .put("body_chars", config.keepaliveBody.length)
            .put("targets", targets)
            .put(
                "next_due_ms",
                if (config.keepaliveEnabled && earliest != null) earliest else JSONObject.NULL
            )

        val inbox = JSONObject()
            .put("collect_sms", config.collectSms)
            .put("collect_calls", config.collectCalls)
            .put("include_body", config.includeBody)
            .put("cap", config.inboxCap)
            .put("pending", Inbox.pending(context))
            .put("dropped", config.inboxDropped)

        val counters = JSONObject()
            .put("sent_ok", config.sentOk)
            .put("sent_failed", config.sentFailed)
            .put("rejected", config.rejected)
            .put("pending", config.pendingCount())

        val lastRecord = recent(context, 1).let {
            if (it.length() > 0) it.getJSONObject(0) else JSONObject.NULL
        }

        return JSONObject()
            .put("schema", 1)
            .put("package", Commands.PKG)
            .put("generated_ms", now)
            .put("ready", isReady(context, config))
            .put("token_set", config.hasToken)
            .put(
                "permissions",
                JSONObject()
                    .put("send_sms", granted(context, Manifest.permission.SEND_SMS))
                    .put("receive_sms", granted(context, Manifest.permission.RECEIVE_SMS))
                    .put("read_call_log", granted(context, Manifest.permission.READ_CALL_LOG))
                    .put(
                        "read_phone_state",
                        granted(context, Manifest.permission.READ_PHONE_STATE),
                    )
            )
            .put(
                "power",
                JSONObject()
                    .put("battery_exempt", Doze.isExempt(context))
                    .put("standby_bucket", Doze.standbyBucket(context)),
            )
            .put("sub_id", config.subId)
            .put("sims", Sims.list(context))
            .put("keepalive", keepalive)
            .put("balances", Balance.report(context))
            .put("inbox", inbox)
            .put("counters", counters)
            .put("last_send", lastRecord)
    }

    /**
     * Whether the unit can actually send right now. This is the single field an
     * unattended host should alert on: everything else is detail.
     */
    private fun isReady(context: Context, config: Config): Boolean =
        config.hasToken &&
            granted(context, Manifest.permission.SEND_SMS) &&
            granted(context, Manifest.permission.RECEIVE_SMS) &&
            // Not decoration: without the exemption the system defers this
            // app's alarms by up to a year, so a unit that can send is still
            // one whose keepalive will not fire.
            Doze.isExempt(context) &&
            Sims.hasActiveSim(context)

    fun granted(context: Context, permission: String): Boolean =
        context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    /**
     * Write through a temporary file and rename.
     *
     * The host may read `status.json` at any moment, including while a command
     * is rewriting it, and a rename is the only way to guarantee it never sees
     * a half-written document.
     */
    private fun writeAtomic(target: File, content: String) {
        val tmp = File(target.parentFile, "${target.name}.tmp")
        runCatching {
            tmp.writeText(content)
            if (!tmp.renameTo(target)) {
                target.writeText(content)
                tmp.delete()
            }
        }
    }

    private fun trim(file: File) {
        runCatching {
            val lines = file.readLines()
            if (lines.size <= OUTBOX_MAX_LINES) return
            writeAtomic(file, lines.takeLast(OUTBOX_MAX_LINES).joinToString("\n") + "\n")
        }
    }
}
