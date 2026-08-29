package com.nktkln.rackphone.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Reading a number out of an operator's balance reply.
 *
 * The strings below are the shapes these actually arrive in - separators,
 * spacing and wording all differ, and the only thing they agree on is that the
 * first number in the message is the one being reported.
 */
class UssdTest {

    @Test
    fun `reads a plain amount`() {
        assertEquals(123.45, Ussd.parseAmount("Balans: 123.45 r")!!, 0.001)
    }

    @Test
    fun `reads a comma as a decimal point`() {
        assertEquals(123.45, Ussd.parseAmount("Ваш баланс 123,45 руб.")!!, 0.001)
    }

    @Test
    fun `reads a grouped thousand, space or non-breaking`() {
        assertEquals(1234.5, Ussd.parseAmount("Баланс: 1 234,50 руб")!!, 0.001)
        assertEquals(1234.5, Ussd.parseAmount("Баланс: 1\u00A0234,50 руб")!!, 0.001)
    }

    @Test
    fun `reads a negative balance, which is the one worth alerting on`() {
        assertEquals(-57.0, Ussd.parseAmount("Balans: -57 rub")!!, 0.001)
    }

    @Test
    fun `takes the reported number, not a later one`() {
        val reply = "Баланс 42,10 руб. Пакет 300 минут действует до 01.09"
        assertEquals(42.10, Ussd.parseAmount(reply)!!, 0.001)
    }

    @Test
    fun `reports nothing rather than guessing when there is no number`() {
        assertNull(Ussd.parseAmount("Услуга временно недоступна"))
        assertNull(Ussd.parseAmount(""))
    }
}

/** When the next balance read is due. */
class BalanceScheduleTest {

    private val hour = 3_600_000L
    private val now = 1_700_000_000_000L

    @Test
    fun `a SIM that was never read is due immediately`() {
        assertEquals(now, Balance.nextRunAt(0L, 24 * hour, now))
    }

    @Test
    fun `otherwise it is an interval after the last reading`() {
        val read = now - 2 * hour
        assertEquals(read + 24 * hour, Balance.nextRunAt(read, 24 * hour, now))
    }

    @Test
    fun `an overdue SIM reports a time in the past rather than pretending`() {
        val read = now - 30 * hour
        assertEquals(read + 24 * hour, Balance.nextRunAt(read, 24 * hour, now))
    }
}
