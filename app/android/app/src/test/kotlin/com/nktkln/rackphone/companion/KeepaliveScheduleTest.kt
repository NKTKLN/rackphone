package com.nktkln.rackphone.companion

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The scheduling arithmetic, which is the part of the keepalive that can be
 * wrong for a month before anyone notices.
 */
class KeepaliveScheduleTest {

    private val hour = 3_600_000L
    private val interval = 720 * hour
    private val now = 1_700_000_000_000L

    @Test
    fun `a unit that has never sent waits a full interval`() {
        assertEquals(now + interval, Keepalive.nextDueAt(0L, interval, now))
    }

    @Test
    fun `a successful send pushes the next one out from that send`() {
        val sent = now - 10 * hour
        assertEquals(sent + interval, Keepalive.nextDueAt(sent, interval, now))
    }

    @Test
    fun `an overdue unit reports a due time in the past rather than pretending`() {
        val sent = now - interval - hour
        assertEquals(sent + interval, Keepalive.nextDueAt(sent, interval, now))
    }

    @Test
    fun `a fresh attempt holds off the next run even when overdue`() {
        val sent = now - interval - hour
        val attempted = now - hour
        val at = Keepalive.nextRunAt(sent, attempted, interval, now)
        assertEquals(attempted + 24 * hour, at)
    }

    @Test
    fun `backoff never delays a run past the schedule`() {
        val sent = now - hour
        val attempted = now - hour
        assertEquals(sent + interval, Keepalive.nextRunAt(sent, attempted, interval, now))
    }

    @Test
    fun `an interval outside the declared range is clamped, not honoured`() {
        assertEquals(Config.MIN_INTERVAL_HOURS, clampInterval(0))
        assertEquals(Config.MAX_INTERVAL_HOURS, clampInterval(100_000))
        assertEquals(24, clampInterval(24))
    }
}

/**
 * Which SIMs a unit keeps alive. On a dual-SIM unit this is the setting that
 * decides whether the quiet SIM is covered at all.
 */
class KeepaliveSubsTest {

    private val active = listOf(1, 2)

    @Test
    fun `all covers every SIM the modem reports`() {
        assertEquals(active, Keepalive.subsFor("all", active, 1))
    }

    @Test
    fun `an empty setting means all, so a fresh install covers both SIMs`() {
        assertEquals(active, Keepalive.subsFor("", active, 1))
    }

    @Test
    fun `default narrows to the subscription Android would send from`() {
        assertEquals(listOf(2), Keepalive.subsFor("default", active, 2))
    }

    @Test
    fun `an explicit list is honoured`() {
        assertEquals(listOf(2), Keepalive.subsFor("2", active, 1))
        assertEquals(listOf(1, 2), Keepalive.subsFor(" 1 , 2 ", active, 1))
    }

    @Test
    fun `a configured SIM that is no longer present is dropped`() {
        // A pulled SIM must not silently hand its keepalive to whichever
        // subscription inherited the slot.
        assertEquals(listOf(1), Keepalive.subsFor("1,7", active, 1))
    }

    @Test
    fun `without READ_PHONE_STATE it still sends on the default`() {
        assertEquals(listOf(3), Keepalive.subsFor("all", emptyList(), 3))
        assertEquals(listOf(1, 2), Keepalive.subsFor("1,2", emptyList(), 3))
    }
}
