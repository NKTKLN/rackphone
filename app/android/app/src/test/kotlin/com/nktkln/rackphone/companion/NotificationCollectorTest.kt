package com.nktkln.rackphone.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationCollectorTest {

    @Test
    fun `skips only notifications sent by this app`() {
        assertTrue(isOwnPackage("com.example.app", "com.example.app"))
        assertFalse(isOwnPackage("com.example.other", "com.example.app"))
    }

    @Test
    fun `skip flags are matched independently`() {
        val ongoing = 2
        val summary = 512
        assertTrue(hasFlag(ongoing, ongoing))
        assertTrue(hasFlag(summary, summary))
        assertTrue(hasFlag(ongoing or summary, ongoing))
        assertFalse(hasFlag(0, ongoing))
    }

    @Test
    fun `only an unchanged repost of the same key is duplicate`() {
        val dedup = NotificationDeduplicator(4)
        assertFalse(dedup.isDuplicate("one", "Title", "Text"))
        assertTrue(dedup.isDuplicate("one", "Title", "Text"))
        assertFalse(dedup.isDuplicate("one", "Changed", "Text"))
        assertFalse(dedup.isDuplicate("two", "Changed", "Text"))
    }

    @Test
    fun `dedup memory stays within its bound`() {
        val dedup = NotificationDeduplicator(3)
        repeat(20) { dedup.isDuplicate("key-$it", "Title", "Text") }
        assertEquals(3, dedup.size())
        assertFalse(dedup.isDuplicate("key-0", "Title", "Text"))
    }
}
