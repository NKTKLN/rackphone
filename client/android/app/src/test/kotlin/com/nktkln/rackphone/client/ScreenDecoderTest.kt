package com.nktkln.rackphone.client

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenDecoderTest {
    @Test
    fun `first config creates and matching config keeps the decoder`() {
        val portrait = DecoderDimensions(1080, 2400)
        assertEquals(ConfigurationAction.CREATE, configurationAction(null, portrait))
        assertEquals(ConfigurationAction.KEEP, configurationAction(portrait, portrait))
    }

    @Test
    fun `a config with either dimension changed resets the decoder`() {
        val portrait = DecoderDimensions(1080, 2400)
        assertEquals(
            ConfigurationAction.RESET,
            configurationAction(portrait, DecoderDimensions(2400, 1080)),
        )
        assertTrue(dimensionsChanged(portrait, DecoderDimensions(1080, 2399)))
        assertFalse(dimensionsChanged(portrait, DecoderDimensions(1080, 2400)))
    }
}
