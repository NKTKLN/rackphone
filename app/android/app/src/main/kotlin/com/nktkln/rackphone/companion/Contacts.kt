package com.nktkln.rackphone.companion

import android.Manifest
import android.content.Context
import android.provider.ContactsContract.CommonDataKinds.Phone
import org.json.JSONArray
import org.json.JSONObject

/** One phone number of one contact, as the address book holds it. */
data class ContactNumber(val name: String, val number: String, val normalized: String?)

/**
 * The address book with duplicates removed and in reading order.
 *
 * A number saved twice to the same person (once formatted, once not, or once
 * per synced account) is one number to the person reading the list.
 */
fun tidyContacts(rows: List<ContactNumber>): List<ContactNumber> =
    rows
        .filter { it.name.isNotBlank() && it.number.isNotBlank() }
        .distinctBy { it.name to (it.normalized ?: digitsOf(it.number)) }
        .sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.name })

private fun digitsOf(number: String): String = number.filter { it.isDigit() || it == '+' }

/**
 * Exports the address book for the host.
 *
 * Read-only by design: the unit's contacts are edited on the unit, and the
 * host only needs names to put next to numbers. The list goes out through a
 * file, as a drained batch does, because a few hundred contacts do not belong
 * in a broadcast result.
 */
object Contacts {
    fun export(context: Context): JSONObject {
        if (!HostFiles.granted(context, Manifest.permission.READ_CONTACTS)) {
            return JSONObject().put("status", "rejected").put("error", "permission_denied")
        }
        val rows = mutableListOf<ContactNumber>()
        val projection = arrayOf(Phone.DISPLAY_NAME, Phone.NUMBER, Phone.NORMALIZED_NUMBER)
        context.contentResolver.query(Phone.CONTENT_URI, projection, null, null, null)
            ?.use { cursor ->
                while (cursor.moveToNext()) {
                    rows.add(
                        ContactNumber(
                            name = cursor.getString(0).orEmpty(),
                            number = cursor.getString(1).orEmpty(),
                            normalized = cursor.getString(2),
                        ),
                    )
                }
            }
        val contacts = tidyContacts(rows)
        val json = JSONArray()
        for (contact in contacts) {
            json.put(
                JSONObject()
                    .put("name", contact.name)
                    .put("number", contact.number)
                    .put("normalized", contact.normalized ?: JSONObject.NULL),
            )
        }
        HostFiles.publishContacts(context, json.toString())
        return JSONObject()
            .put("status", "exported")
            .put("count", contacts.size)
            .put("file", HostFiles.contactsFile(context).absolutePath)
    }
}
