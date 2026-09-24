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
    private var callAudio: CallAudio? = null
    private val fileChooser = FileChooser(this)

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Video is roughly a megabyte per second. A MethodChannel would encode every
        // payload through StandardMessageCodec; BinaryCodec passes these bytes through.
        val screenChannel = BasicMessageChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL,
            BinaryCodec.INSTANCE,
        )
        val decoder = ScreenDecoder(flutterEngine.renderer) { width, height ->
            screenChannel.send(sizeMessage(width, height))
        }
        screenDecoder = decoder
        screenChannel.setMessageHandler { message, reply ->
            reply.reply(message?.let { dispatch(decoder, it.order(ByteOrder.BIG_ENDIAN)) })
        }

        val audioChannel = BasicMessageChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CALL_AUDIO_CHANNEL,
            BinaryCodec.INSTANCE,
        )
        val audio = CallAudio(this, audioChannel)
        callAudio = audio
        audioChannel.setMessageHandler { message, reply ->
            if (message == null || !message.hasRemaining()) {
                reply.reply(null)
            } else {
                val ordered = message.order(ByteOrder.BIG_ENDIAN)
                when (ordered.get().toInt()) {
                    START_AUDIO -> audio.start(ordered.int, ordered.int, reply)
                    PLAY_AUDIO -> {
                        val packet = ByteArray(ordered.remaining())
                        ordered.get(packet)
                        audio.play(packet)
                        reply.reply(null)
                    }
                    STOP_AUDIO -> {
                        audio.dispose()
                        reply.reply(null)
                    }
                    else -> reply.reply(null)
                }
            }
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

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        if (callAudio?.onRequestPermissionsResult(requestCode, grantResults) == true) return
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        screenDecoder?.dispose()
        screenDecoder = null
        callAudio?.close()
        callAudio = null
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

    // Not flipped, like the reply below: the length is the position.
    private fun sizeMessage(width: Int, height: Int): ByteBuffer =
        ByteBuffer.allocateDirect(SIZE_MESSAGE_BYTES)
            .order(ByteOrder.BIG_ENDIAN)
            .put(SIZE.toByte())
            .putInt(width)
            .putInt(height)

    // Not flipped: the embedding takes a binary reply's length from its
    // position, so a flipped buffer arrives in Dart as zero bytes.
    private fun textureReply(textureId: Long): ByteBuffer =
        ByteBuffer.allocateDirect(Long.SIZE_BYTES)
            .order(ByteOrder.BIG_ENDIAN)
            .putLong(textureId)

    private companion object {
        const val CHANNEL = "com.nktkln.rackphone.client/screen"
        const val FILES_CHANNEL = "com.nktkln.rackphone.client/files"
        const val CALL_AUDIO_CHANNEL = "com.nktkln.rackphone.client/call_audio"
        const val CREATE = 0
        const val FEED = 1
        const val RESET = 2
        const val DISPOSE = 3

        // From the decoder to Dart: the picture's size changed.
        const val SIZE = 4
        const val SIZE_MESSAGE_BYTES = 9
        const val START_AUDIO = 0
        const val PLAY_AUDIO = 1
        const val STOP_AUDIO = 2
    }
}
