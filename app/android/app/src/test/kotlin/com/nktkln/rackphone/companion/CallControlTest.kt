package com.nktkln.rackphone.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CallControlTest {
    @Test
    fun `only entering ringing produces a ringing event`() {
        assertTrue(shouldRecordRinging(false, CallState.RINGING))
        assertFalse(shouldRecordRinging(true, CallState.RINGING))
        assertFalse(shouldRecordRinging(false, CallState.OFFHOOK))
        assertFalse(shouldRecordRinging(false, CallState.IDLE))
    }

    @Test
    fun `ringing id is carried to the outcome`() {
        assertEquals("call-1", correlatedCallId("call-1", "call-2"))
        assertEquals("call-2", correlatedCallId("", "call-2"))
    }

    @Test
    fun `call operation reports what actually happened`() {
        assertEquals("answered", callOutcome(CallOperation.ANSWER, true, true))
        assertEquals("rejected", callOutcome(CallOperation.REJECT, true, true))
        assertEquals("no_ringing_call", callOutcome(CallOperation.ANSWER, false, false))
        assertEquals("failed", callOutcome(CallOperation.REJECT, true, false))
    }

    @Test
    fun `only keypad keys can be sent as tones`() {
        assertTrue(isDtmfDigits("1"))
        assertTrue(isDtmfDigits("*102#"))
        assertFalse(isDtmfDigits(""))
        assertFalse(isDtmfDigits("12a"))
        assertFalse(isDtmfDigits("1".repeat(33)))
    }

    @Test
    fun `only a blank ringing later given a number is named again`() {
        assertTrue(namesCallerLate(true, UNKNOWN_CALLER, "+79001234567"))
        assertFalse(namesCallerLate(false, UNKNOWN_CALLER, "+79001234567"))
        assertFalse(namesCallerLate(true, "+79001234567", "+79001234567"))
        assertFalse(namesCallerLate(true, UNKNOWN_CALLER, ""))
        assertFalse(namesCallerLate(true, UNKNOWN_CALLER, null))
    }
}
