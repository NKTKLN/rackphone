package com.nktkln.rackphone.companion

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * What arrived, waiting for the host to take it.
 *
 * The delivery contract is the one the host already speaks: `drain` moves the
 * spool aside to an in-flight file and prints it, and the events are deleted
 * only when the host calls `ack` - which it does after committing them. An
 * interruption anywhere in between re-delivers the batch rather than losing it.
 * The host absorbs the duplicate with a UNIQUE constraint, because a duplicate
 * notification is an annoyance and a dropped SMS is invisible.
 *
 * The app decides nothing about what happens next. It has no ntfy credential,
 * no store and no policy: it collects, and the host relays.
 */
object Inbox {
    private const val SPOOL = "inbox.jsonl"
    private const val INFLIGHT = "inbox.inflight"

    /**
     * The event id the host dedups on. It has to be an integer, and it has to
     * keep climbing across a reinstall: a counter that restarted at 1 would
     * collide with events the host stored months ago, and the duplicate would
     * be silently dropped - losing the new message, not the old one. Seeding
     * from the clock costs nothing and cannot go backwards.
     */
    private fun nextId(context: Context): Long {
        val config = Config.of(context)
        synchronized(this) {
            val next = maxOf(config.inboxSequence + 1, System.currentTimeMillis() / 1000)
            config.inboxSequence = next
            return next
        }
    }

    /**
     * Spool one event.
     *
     * @param event The event, without its id: this assigns it.
     */
    fun record(context: Context, event: JSONObject) {
        val config = Config.of(context)
        event.put("id", nextId(context))
        synchronized(this) {
            runCatching { spool(context).appendText(event.toString() + "\n") }
            trim(context, config.inboxCap)
        }
    }

    /** Hand the pending batch to the host, keeping it until it is acked. */
    fun drain(context: Context): String = synchronized(this) {
        val inflight = inflight(context)
        if (!inflight.exists()) {
            val spool = spool(context)
            if (!spool.exists()) return ""
            // An unacked batch is re-emitted before anything new is taken, so
            // ordering survives a failed transfer.
            if (!spool.renameTo(inflight)) return ""
        }
        runCatching { inflight.readText() }.getOrDefault("")
    }

    /** Confirm the last drain. Only now is anything deleted. */
    fun ack(context: Context) {
        synchronized(this) { inflight(context).delete() }
    }

    /** Everything pending, without consuming it. */
    fun peek(context: Context): String = synchronized(this) {
        val parts = listOf(inflight(context), spool(context))
            .filter { it.exists() }
            .mapNotNull { runCatching { it.readText() }.getOrNull() }
        parts.joinToString("")
    }

    /** Drop everything pending, for a unit that must start from now. */
    fun reset(context: Context) {
        synchronized(this) {
            spool(context).delete()
            inflight(context).delete()
        }
    }

    fun pending(context: Context): Int =
        countLines(spool(context)) + countLines(inflight(context))

    private fun spool(context: Context) = File(HostFiles.dir(context), SPOOL)

    private fun inflight(context: Context) = File(HostFiles.dir(context), INFLIGHT)

    private fun countLines(file: File): Int =
        if (!file.exists()) 0
        else runCatching { file.readLines().count { it.isNotBlank() } }.getOrDefault(0)

    /**
     * Bound the spool so a host that stopped draining cannot fill the data
     * partition. Oldest first, and the loss is counted rather than silent.
     */
    private fun trim(context: Context, cap: Int) {
        val file = spool(context)
        runCatching {
            val lines = file.readLines()
            if (lines.size <= cap) return
            val dropped = lines.size - cap
            file.writeText(lines.takeLast(cap).joinToString("\n") + "\n")
            Config.of(context).addDropped(dropped)
        }
    }
}
