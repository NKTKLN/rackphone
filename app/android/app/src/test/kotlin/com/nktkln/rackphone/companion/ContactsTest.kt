package com.nktkln.rackphone.companion

import org.junit.Assert.assertEquals
import org.junit.Test

class ContactsTest {

    @Test
    fun `a number saved twice to one person is listed once`() {
        val tidy = tidyContacts(
            listOf(
                ContactNumber("Andrew", "+7 900 123-45-67", "+79001234567"),
                ContactNumber("Andrew", "+79001234567", "+79001234567"),
                ContactNumber("Andrew", "8 900 000-00-00", null),
            ),
        )
        assertEquals(2, tidy.size)
    }

    @Test
    fun `nameless and numberless rows are dropped`() {
        val tidy = tidyContacts(
            listOf(
                ContactNumber("", "+7900", null),
                ContactNumber("Olga", " ", null),
                ContactNumber("Olga", "+7901", null),
            ),
        )
        assertEquals(listOf("+7901"), tidy.map { it.number })
    }

    @Test
    fun `contacts come out in reading order whatever the case`() {
        val tidy = tidyContacts(
            listOf(
                ContactNumber("olga", "1", null),
                ContactNumber("Andrew", "2", null),
                ContactNumber("MTS", "3", null),
            ),
        )
        assertEquals(listOf("Andrew", "MTS", "olga"), tidy.map { it.name })
    }
}
