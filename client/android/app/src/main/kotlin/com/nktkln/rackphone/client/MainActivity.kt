package com.nktkln.rackphone.client

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import android.content.Intent
import io.flutter.plugin.common.BasicMessageChannel
import io.flutter.plugin.common.BinaryCodec
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Hosts the low-overhead binary bridge between the screen stream and MediaCodec. */
class MainActivity : FlutterActivity() {
    private var screenDecoder: ScreenDecoder? = null
    private val fileChooser = FileChooser(this)

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val decoder = ScreenDecoder(flutterEngine.renderer)
        screenDecoder = decoder
        // Video is roughly a megabyte per second. A MethodChannel would encode every
        // payload through StandardMessageCodec; BinaryCodec passes these bytes through.
        BasicMessageChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL,
            BinaryCodec.INSTANCE,
        ).setMessageHandler { message, reply ->
            reply.reply(message?.let { dispatch(decoder, it.order(ByteOrder.BIG_ENDIAN)) })
        }

        // A method channel here, not a binary one: these calls are two a day and
        // carry a name beside the bytes, so the standard codec earns its keep.
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            FILES_CHANNEL,
        ).setMethodCallHandler(fileChooser::handle)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (fileChooser.onActivityResult(requestCode, resultCode, data)) return
        super.onActivityResult(requestCode, resultCode, data)
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        screenDecoder?.dispose()
        screenDecoder = null
        super.cleanUpFlutterEngine(flutterEngine)
    }

    private fun dispatch(decoder: ScreenDecoder, message: ByteBuffer): ByteBuffer? {
        if (!message.hasRemaining()) return null
        return when (message.get().toInt()) {
            CREATE -> textureReply(decoder.create(message.int, message.int))
            FEED -> {
                val isConfig = message.get().toInt() != 0
                val packet = ByteArray(message.remaining())
                message.get(packet)
                decoder.feed(packet, isConfig)
                null
            }
            RESET -> {
                decoder.reset(message.int, message.int)
                null
            }
            DISPOSE -> {
                decoder.dispose()
                null
            }
            else -> null
        }
    }

    private fun textureReply(textureId: Long): ByteBuffer =
        ByteBuffer.allocateDirect(Long.SIZE_BYTES)
            .order(ByteOrder.BIG_ENDIAN)
            .putLong(textureId)
            .also { it.flip() }

    private companion object {
        const val CHANNEL = "com.nktkln.rackphone.client/screen"
        const val FILES_CHANNEL = "com.nktkln.rackphone.client/files"
        const val CREATE = 0
        const val FEED = 1
        const val RESET = 2
        const val DISPOSE = 3
    }
}
