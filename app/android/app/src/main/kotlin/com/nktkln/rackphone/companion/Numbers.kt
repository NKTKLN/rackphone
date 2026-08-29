package com.nktkln.rackphone.companion

/**
 * Phone number handling.
 *
 * Deliberately not a validator. The unit sends to whatever the operator
 * accepts, which includes short codes (`900`), so the only job here is to strip
 * the punctuation a human types and refuse input that cannot be a destination
 * at all. Rejecting a number the network would have accepted is the worse
 * failure of the two: this app exists precisely to keep a SIM alive.
 */
object Numbers {
    private const val MAX_DIGITS = 20

    /** Grouping characters a number is commonly written with. */
    private val PUNCTUATION =
        charArrayOf(' ', '\u00A0', '-', '\u2013', '(', ')', '.', '/')

    /**
     * Strip formatting and check the result can be dialled.
     *
     * @return the cleaned number, or null if it is not usable.
     */
    fun sanitise(raw: String?): String? {
        val trimmed = raw?.trim() ?: return null
        if (trimmed.isEmpty()) return null

        val builder = StringBuilder(trimmed.length)
        for ((index, ch) in trimmed.withIndex()) {
            when {
                ch in PUNCTUATION -> continue
                ch == '+' && index == 0 -> builder.append(ch)
                ch.isDigit() -> builder.append(ch)
                // Anything else - a letter, a second '+', a control character -
                // means this was never a phone number.
                else -> return null
            }
        }

        val cleaned = builder.toString()
        val digits = cleaned.count { it.isDigit() }
        if (digits == 0 || digits > MAX_DIGITS) return null
        return cleaned
    }
}
