package com.nktkln.rackphone.companion

import android.content.Context
import android.content.SharedPreferences
import java.security.SecureRandom

/**
 * Everything the app remembers, in SharedPreferences.
 *
 * Native code owns the settings rather than Dart, because a broadcast arrives
 * when the Flutter engine is not running: the receiver has to be able to read
 * the keepalive schedule without starting a UI. The Dart side reaches this
 * through the method channel, so there is still one copy of every value.
 */
class Config private constructor(private val prefs: SharedPreferences) {

    companion object {
        private const val NAME = "rackphone"

        private const val KEY_TOKEN = "token"
        private const val KEY_SUB = "sub_id"
        private const val KEY_KEEPALIVE_ENABLED = "keepalive_enabled"
        private const val KEY_KEEPALIVE_TO = "keepalive_to"
        private const val KEY_KEEPALIVE_INTERVAL = "keepalive_interval_hours"
        private const val KEY_KEEPALIVE_BODY = "keepalive_body"
        private const val KEY_KEEPALIVE_SUBS = "keepalive_subs"
        private const val PREFIX_LAST_SUCCESS = "last_success_ms."
        private const val PREFIX_LAST_ATTEMPT = "last_attempt_ms."
        private const val KEY_REQUEST_CODE = "request_code"
        private const val KEY_COLLECT_SMS = "collect_sms"
        private const val KEY_COLLECT_CALLS = "collect_calls"
        private const val KEY_INCLUDE_BODY = "include_body"
        private const val KEY_INBOX_CAP = "inbox_cap"
        private const val KEY_INBOX_SEQUENCE = "inbox_sequence"
        private const val KEY_INBOX_DROPPED = "inbox_dropped"
        private const val KEY_BALANCE_CODE = "balance_code"
        private const val KEY_BALANCE_INTERVAL = "balance_interval_hours"
        private const val PREFIX_BALANCE_TEXT = "balance_text."
        private const val PREFIX_BALANCE_VALUE = "balance_value."
        private const val PREFIX_BALANCE_AT = "balance_at."
        private const val KEY_RINGING_FROM = "ringing_from"
        private const val KEY_RINGING_SINCE = "ringing_since_ms"
        private const val KEY_CALL_ANSWERED = "call_answered_ms"
        private const val KEY_SENT_OK = "counter_sent_ok"
        private const val KEY_SENT_FAILED = "counter_sent_failed"
        private const val KEY_REJECTED = "counter_rejected"

        /** Any SIM. `SubscriptionManager.getDefaultSmsSubscriptionId()` decides. */
        const val SUB_DEFAULT = -1

        /**
         * Thirty days. Operators that expire an idle SIM give 90 to 180 days, so
         * a month leaves two missed runs of headroom before anything is at risk,
         * while still costing twelve messages a year.
         */
        const val DEFAULT_INTERVAL_HOURS = 720
        const val MIN_INTERVAL_HOURS = 1
        const val MAX_INTERVAL_HOURS = 8760

        /**
         * Plain ASCII on purpose: a GSM-7 body is one part and one charge, and a
         * single Cyrillic character would switch the whole message to UCS-2.
         */
        const val DEFAULT_KEEPALIVE_BODY = "rackphone keepalive"

        /**
         * How many events may wait for a host that stopped draining. Two
         * thousand is what the shell collector used, and the reasoning carries
         * over: enough for a long outage, small enough that the data partition
         * is never the thing that fails.
         */
        const val DEFAULT_INBOX_CAP = 2000

        /**
         * Beeline's balance code, because that is what the units in this rack
         * carry. Every operator has its own, so this is a setting rather than
         * a constant - and an empty one turns the checks off.
         */
        const val DEFAULT_BALANCE_CODE = "*102#"

        /**
         * Daily. A prepaid SIM does not run out in an hour, and a USSD request
         * is a network round trip on a device whose whole point is to be left
         * alone.
         */
        const val DEFAULT_BALANCE_INTERVAL_HOURS = 24

        /** `keepalive_to` value meaning "this unit's own number". */
        const val TARGET_SELF = "self"

        /**
         * `keepalive_subs` values. A dual-SIM unit loses either SIM the same
         * way, so the default keeps every one of them alive rather than only
         * the one Android happens to send from.
         */
        const val SUBS_ALL = "all"
        const val SUBS_DEFAULT = "default"

        fun of(context: Context): Config =
            Config(
                context.applicationContext
                    .getSharedPreferences(NAME, Context.MODE_PRIVATE)
            )
    }

    /** The shared secret every broadcast must carry. Empty until setup. */
    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TOKEN, value).apply()

    val hasToken: Boolean get() = token.isNotEmpty()

    /** Which SIM sends, or [SUB_DEFAULT] to let Android pick. */
    var subId: Int
        get() = prefs.getInt(KEY_SUB, SUB_DEFAULT)
        set(value) = prefs.edit().putInt(KEY_SUB, value).apply()

    var keepaliveEnabled: Boolean
        get() = prefs.getBoolean(KEY_KEEPALIVE_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_KEEPALIVE_ENABLED, value).apply()

    /** Destination, or [TARGET_SELF] to text the unit's own number. */
    var keepaliveTo: String
        get() = prefs.getString(KEY_KEEPALIVE_TO, TARGET_SELF) ?: TARGET_SELF
        set(value) = prefs.edit().putString(KEY_KEEPALIVE_TO, value).apply()

    var keepaliveIntervalHours: Int
        get() = prefs.getInt(KEY_KEEPALIVE_INTERVAL, DEFAULT_INTERVAL_HOURS)
        set(value) = prefs.edit().putInt(KEY_KEEPALIVE_INTERVAL, clampInterval(value)).apply()

    var keepaliveBody: String
        get() = prefs.getString(KEY_KEEPALIVE_BODY, DEFAULT_KEEPALIVE_BODY)
            ?: DEFAULT_KEEPALIVE_BODY
        set(value) = prefs.edit().putString(KEY_KEEPALIVE_BODY, value).apply()

    /** Which SIMs to keep alive: [SUBS_ALL], [SUBS_DEFAULT], or `1,2`. */
    var keepaliveSubs: String
        get() = prefs.getString(KEY_KEEPALIVE_SUBS, SUBS_ALL) ?: SUBS_ALL
        set(value) = prefs.edit().putString(KEY_KEEPALIVE_SUBS, value.trim()).apply()

    /**
     * When the radio last accepted a message *on this subscription*, whoever
     * asked for it. Per SIM and not per device, because an operator only ever
     * sees its own SIM: keeping one alive says nothing about the other, and a
     * device-wide counter would let the busy SIM hide the idle one.
     *
     * The keepalive counts any successful send as activity, so a SIM the host
     * already sent through this month sends nothing extra.
     */
    fun lastSuccessMs(sub: Int): Long = prefs.getLong(PREFIX_LAST_SUCCESS + sub, 0L)

    fun recordSuccess(sub: Int, atMs: Long) {
        prefs.edit().putLong(PREFIX_LAST_SUCCESS + sub, atMs).apply()
    }

    /**
     * When the keepalive last tried on this subscription, successful or not.
     * Without it a SIM whose target cannot be resolved would be permanently
     * overdue, and the alarm would re-fire as fast as the system allows.
     */
    fun lastAttemptMs(sub: Int): Long = prefs.getLong(PREFIX_LAST_ATTEMPT + sub, 0L)

    fun recordAttempt(sub: Int, atMs: Long) {
        prefs.edit().putLong(PREFIX_LAST_ATTEMPT + sub, atMs).apply()
    }

    val keepaliveIntervalMs: Long get() = keepaliveIntervalHours * 3_600_000L

    /** Whether arriving SMS are spooled for the host. */
    var collectSms: Boolean
        get() = prefs.getBoolean(KEY_COLLECT_SMS, true)
        set(value) = prefs.edit().putBoolean(KEY_COLLECT_SMS, value).apply()

    /** Whether missed and answered incoming calls are spooled for the host. */
    var collectCalls: Boolean
        get() = prefs.getBoolean(KEY_COLLECT_CALLS, true)
        set(value) = prefs.edit().putBoolean(KEY_COLLECT_CALLS, value).apply()

    /**
     * Off relays the sender and the time without the text. The host then knows
     * a message arrived, from whom and when, and the content never leaves the
     * phone.
     */
    var includeBody: Boolean
        get() = prefs.getBoolean(KEY_INCLUDE_BODY, true)
        set(value) = prefs.edit().putBoolean(KEY_INCLUDE_BODY, value).apply()

    var inboxCap: Int
        get() = prefs.getInt(KEY_INBOX_CAP, DEFAULT_INBOX_CAP)
        set(value) = prefs.edit().putInt(KEY_INBOX_CAP, value.coerceAtLeast(1)).apply()

    /** The id handed to the next spooled event. See [Inbox]. */
    var inboxSequence: Long
        get() = prefs.getLong(KEY_INBOX_SEQUENCE, 0L)
        set(value) = prefs.edit().putLong(KEY_INBOX_SEQUENCE, value).apply()

    val inboxDropped: Int get() = prefs.getInt(KEY_INBOX_DROPPED, 0)

    fun addDropped(count: Int) {
        prefs.edit().putInt(KEY_INBOX_DROPPED, inboxDropped + count).apply()
    }

    /**
     * The number of the call currently ringing, remembered between the two
     * broadcasts that make up a missed call. Empty when nothing is ringing.
     */
    var ringingFrom: String
        get() = prefs.getString(KEY_RINGING_FROM, "") ?: ""
        set(value) = prefs.edit().putString(KEY_RINGING_FROM, value).apply()

    /** When the ringing call started, which is when the event happened. */
    var ringingSinceMs: Long
        get() = prefs.getLong(KEY_RINGING_SINCE, 0L)
        set(value) = prefs.edit().putLong(KEY_RINGING_SINCE, value).apply()

    /**
     * When the ringing call was picked up, or 0 if it never was. Being picked
     * up is the whole difference between an incoming call and a missed one.
     */
    var callAnsweredMs: Long
        get() = prefs.getLong(KEY_CALL_ANSWERED, 0L)
        set(value) = prefs.edit().putLong(KEY_CALL_ANSWERED, value).apply()

    /** USSD code that reports the balance, or empty to never ask. */
    var balanceCode: String
        get() = prefs.getString(KEY_BALANCE_CODE, DEFAULT_BALANCE_CODE) ?: DEFAULT_BALANCE_CODE
        set(value) = prefs.edit().putString(KEY_BALANCE_CODE, value.trim()).apply()

    var balanceIntervalHours: Int
        get() = prefs.getInt(KEY_BALANCE_INTERVAL, DEFAULT_BALANCE_INTERVAL_HOURS)
        set(value) = prefs.edit().putInt(KEY_BALANCE_INTERVAL, value.coerceIn(1, 8760)).apply()

    val balanceIntervalMs: Long get() = balanceIntervalHours * 3_600_000L

    /**
     * The last balance reply for one SIM: what the operator said, the number
     * pulled out of it if there was one, and when. The raw text is kept
     * because the parse is a guess and the operator's wording is the evidence.
     */
    fun recordBalance(sub: Int, text: String, amount: Double?) {
        prefs.edit()
            .putString(PREFIX_BALANCE_TEXT + sub, text)
            .putString(PREFIX_BALANCE_VALUE + sub, amount?.toString() ?: "")
            .putLong(PREFIX_BALANCE_AT + sub, System.currentTimeMillis())
            .apply()
    }

    fun balanceText(sub: Int): String = prefs.getString(PREFIX_BALANCE_TEXT + sub, "") ?: ""

    fun balanceValue(sub: Int): Double? =
        prefs.getString(PREFIX_BALANCE_VALUE + sub, "")?.toDoubleOrNull()

    fun balanceAt(sub: Int): Long = prefs.getLong(PREFIX_BALANCE_AT + sub, 0L)

    fun countSentOk(): Int = bump(KEY_SENT_OK)

    fun countSentFailed(): Int = bump(KEY_SENT_FAILED)

    fun countRejected(): Int = bump(KEY_REJECTED)

    val sentOk: Int get() = prefs.getInt(KEY_SENT_OK, 0)
    val sentFailed: Int get() = prefs.getInt(KEY_SENT_FAILED, 0)
    val rejected: Int get() = prefs.getInt(KEY_REJECTED, 0)

    /**
     * Hand out a PendingIntent request code nothing else is using.
     *
     * Two PendingIntents are the same object to the system when their intents
     * and request codes match; the extras are not compared. Reusing a code
     * would therefore overwrite the extras of a send still in flight, and the
     * result of one part would be attributed to another.
     */
    fun nextRequestCode(): Int {
        val next = prefs.getInt(KEY_REQUEST_CODE, 1) + 1
        prefs.edit().putInt(KEY_REQUEST_CODE, next).apply()
        return next
    }

    /** Store the in-flight parts of one send, keyed by its id. */
    fun putPending(id: String, json: String) {
        prefs.edit().putString(pendingKey(id), json).apply()
    }

    fun pending(id: String): String? = prefs.getString(pendingKey(id), null)

    fun clearPending(id: String) {
        prefs.edit().remove(pendingKey(id)).apply()
    }

    fun pendingCount(): Int = prefs.all.keys.count { it.startsWith(PENDING_PREFIX) }

    /** Generate and store a fresh token, returning it. */
    fun rotateToken(): String {
        val bytes = ByteArray(24)
        SecureRandom().nextBytes(bytes)
        val fresh = bytes.joinToString("") { "%02x".format(it) }
        token = fresh
        return fresh
    }

    private fun bump(key: String): Int {
        val next = prefs.getInt(key, 0) + 1
        prefs.edit().putInt(key, next).apply()
        return next
    }

    private fun pendingKey(id: String) = "$PENDING_PREFIX$id"
}

private const val PENDING_PREFIX = "pending."

/** Keep an interval inside the declared range whatever the caller passed. */
fun clampInterval(hours: Int): Int =
    hours.coerceIn(Config.MIN_INTERVAL_HOURS, Config.MAX_INTERVAL_HOURS)
