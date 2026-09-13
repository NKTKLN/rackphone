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
}
