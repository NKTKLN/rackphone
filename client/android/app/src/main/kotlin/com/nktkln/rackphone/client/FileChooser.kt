package com.nktkln.rackphone.client

import android.app.Activity
import android.content.Intent
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * The system document picker, reached directly rather than through a package.
 *
 * Choosing a file is the one thing this app cannot do from Dart, and it is two
 * intents on the one platform this app targets. A picker package would put a
 * dependency this project does not control between an operator and a transfer -
 * and the obvious candidate does not build against the current Android Gradle
 * plugin at all, which is how this ended up here.
 */
class FileChooser(private val activity: Activity) {
    private var pending: MethodChannel.Result? = null
    private var pendingName: String? = null
    private var pendingBytes: ByteArray? = null

    /** Routes one channel call, answering later from [onActivityResult]. */
    fun handle(call: MethodCall, result: MethodChannel.Result) {
        // One request at a time: a second dialog would leave the first result
        // with nobody to answer it, and Flutter throws when a result is dropped.
        if (pending != null) {
            result.error("busy", "A file dialog is already open", null)
            return
        }
        when (call.method) {
            "pick" -> {
                pending = result
                activity.startActivityForResult(openDocument(), REQUEST_PICK)
            }
            "save" -> {
                val name = call.argument<String>("name")
                val bytes = call.argument<ByteArray>("bytes")
                if (name == null || bytes == null) {
                    result.error("bad_request", "name and bytes are required", null)
                    return
                }
                pending = result
                pendingName = name
                pendingBytes = bytes
                activity.startActivityForResult(createDocument(name), REQUEST_SAVE)
            }
            else -> result.notImplemented()
        }
    }

    /**
     * Completes the pending call.
     *
     * Returns whether this request was ours, so the activity can pass anything
     * else on rather than swallowing another component's result.
     */
    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?): Boolean {
        if (requestCode != REQUEST_PICK && requestCode != REQUEST_SAVE) return false
        val result = pending ?: return true
        pending = null
        val name = pendingName
        val bytes = pendingBytes
        pendingName = null
        pendingBytes = null

        val uri = data?.data
        // A cancelled dialog is an answer, not a failure: null tells Dart the
        // operator changed their mind, and an error there would look like a bug.
        if (resultCode != Activity.RESULT_OK || uri == null) {
            result.success(null)
            return true
        }
        try {
            if (requestCode == REQUEST_PICK) {
                val content = activity.contentResolver.openInputStream(uri)?.use {
                    it.readBytes()
                } ?: throw IllegalStateException("the chosen file could not be read")
                result.success(mapOf("name" to displayName(uri), "bytes" to content))
            } else {
                activity.contentResolver.openOutputStream(uri)?.use {
                    it.write(bytes ?: ByteArray(0))
                } ?: throw IllegalStateException("the destination could not be opened")
                result.success(name)
            }
        } catch (error: Exception) {
            result.error("io", error.message, null)
        }
        return true
    }

    private fun openDocument(): Intent =
        Intent(Intent.ACTION_OPEN_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*")

    private fun createDocument(name: String): Intent =
        Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/octet-stream")
            .putExtra(Intent.EXTRA_TITLE, name)

    private fun displayName(uri: android.net.Uri): String {
        val cursor = activity.contentResolver.query(uri, null, null, null, null)
        cursor?.use {
            val column = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (column >= 0 && it.moveToFirst()) return it.getString(column)
        }
        // Falling back to the last path segment rather than failing: the name is
        // a label for the operator, and a transfer is not worth refusing over it.
        return uri.lastPathSegment ?: "file"
    }

    private companion object {
        const val REQUEST_PICK = 0x5250
        const val REQUEST_SAVE = 0x5251
    }
}
