package com.nktkln.rackphone.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The destination is the one field a host can get wrong in a way that is
 * expensive: a number that survives sanitising is handed straight to the radio.
 */
class NumbersTest {

    @Test
    fun `keeps a plain international number`() {
        assertEquals("+79001234567", Numbers.sanitise("+79001234567"))
    }

    @Test
    fun `strips the punctuation a person types`() {
        assertEquals("+79001234567", Numbers.sanitise(" +7 (900) 123-45-67 "))
    }

    @Test
    fun `keeps short codes, which operators use for exactly this`() {
        assertEquals("900", Numbers.sanitise("900"))
    }

    @Test
    fun `rejects letters, so an unresolved template never reaches the radio`() {
        assertNull(Numbers.sanitise("+7900SELF"))
        assertNull(Numbers.sanitise("self"))
    }

    @Test
    fun `rejects a plus that is not leading`() {
        assertNull(Numbers.sanitise("790+01234567"))
    }

    @Test
    fun `rejects empty and absent input`() {
        assertNull(Numbers.sanitise(null))
        assertNull(Numbers.sanitise("   "))
        assertNull(Numbers.sanitise("+"))
    }

    @Test
    fun `rejects more digits than any numbering plan has`() {
        assertNull(Numbers.sanitise("1".repeat(21)))
    }
}
