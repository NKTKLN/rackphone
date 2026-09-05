package com.nktkln.rackphone.client

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import io.flutter.view.TextureRegistry

/** Dimensions attached to the codec instance, kept Android-free for JVM tests. */
data class DecoderDimensions(val width: Int, val height: Int)

/** The native operation required by a newly announced stream configuration. */
enum class ConfigurationAction { CREATE, RESET, KEEP }

/**
 * Decides whether dimensions announced with a config packet require a rebuild.
 * A rotation is detected here, before corrupt output from the old codec can be shown.
 */
fun configurationAction(
    current: DecoderDimensions?,
    announced: DecoderDimensions,
): ConfigurationAction =
    when {
        current == null -> ConfigurationAction.CREATE
        dimensionsChanged(current, announced) -> ConfigurationAction.RESET
        else -> ConfigurationAction.KEEP
    }

/** Exact comparison matters because either changed edge describes a new output surface. */
fun dimensionsChanged(current: DecoderDimensions, announced: DecoderDimensions): Boolean =
    current.width != announced.width || current.height != announced.height

/**
 * Owns Android's hardware AVC decoder and the Flutter texture receiving its output.
 *
 * MediaCodec is used directly because the stream is already framed Annex-B H.264;
 * another video package would add buffering and another lifecycle around the platform
 * decoder without doing useful demuxing work.
 */
class ScreenDecoder(private val textures: TextureRegistry) {
    private var texture: TextureRegistry.SurfaceTextureEntry? = null
    private var surface: Surface? = null
    private var codec: MediaCodec? = null
    private var dimensions: DecoderDimensions? = null
    private var presentationTimeUs = 0L

    /** Registers the composable texture, configures AVC output, and starts decoding. */
    fun create(width: Int, height: Int): Long {
        check(codec == null) { "Screen decoder already exists" }
        val entry = textures.createSurfaceTexture()
        texture = entry
        entry.surfaceTexture().setDefaultBufferSize(width, height)
        surface = Surface(entry.surfaceTexture())
        buildCodec(width, height)
        return entry.id()
    }

    /**
     * Queues one complete Annex-B access unit and renders every available output.
     * Input waits briefly: an infinite wait on Flutter's platform thread would freeze
     * the interface as soon as the phone stopped producing frames.
     */
    fun feed(packet: ByteArray, isConfig: Boolean) {
        val activeCodec = codec ?: return
        val index = activeCodec.dequeueInputBuffer(INPUT_TIMEOUT_US)
        if (index < 0) return
        val input = activeCodec.getInputBuffer(index) ?: return
        input.clear()
        // A packet larger than the buffer the codec handed back would throw
        // BufferOverflowException out of put(). Key frames at a high bitrate
        // are where that happens, so the failure would arrive as a crash on the
        // one frame the picture cannot resume without. Returning the buffer
        // unused costs a frame; the decoder recovers on the next key frame.
        if (packet.size > input.remaining()) {
            activeCodec.queueInputBuffer(index, 0, 0, presentationTimeUs, 0)
            return
        }
        input.put(packet)
        // This flag is essential: without it MediaCodec never learns the SPS/PPS,
        // silently rejects every later frame, and leaves only a black texture.
        val flags = if (isConfig) MediaCodec.BUFFER_FLAG_CODEC_CONFIG else 0
        activeCodec.queueInputBuffer(index, 0, packet.size, presentationTimeUs++, flags)
        drain(activeCodec)
    }

    /** Rebuilds for a rotation while retaining the texture id used by Flutter. */
    fun reset(width: Int, height: Int) {
        check(texture != null) { "Screen decoder has not been created" }
        releaseCodec()
        texture!!.surfaceTexture().setDefaultBufferSize(width, height)
        buildCodec(width, height)
    }

    /** Releases every native resource; repeated disposal is deliberately harmless. */
    fun dispose() {
        releaseCodec()
        surface?.release()
        surface = null
        texture?.release()
        texture = null
        dimensions = null
    }

    private fun buildCodec(width: Int, height: Int) {
        require(width > 0 && height > 0) { "Screen dimensions must be positive" }
        val output = checkNotNull(surface)
        val created = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        try {
            created.configure(
                MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height),
                output,
                null,
                0,
            )
            created.start()
            codec = created
            dimensions = DecoderDimensions(width, height)
            presentationTimeUs = 0
        } catch (error: RuntimeException) {
            created.release()
            throw error
        }
    }

    private fun drain(activeCodec: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        while (true) {
            val index = activeCodec.dequeueOutputBuffer(info, 0)
            if (index < 0) return
            activeCodec.releaseOutputBuffer(index, true)
        }
    }

    private fun releaseCodec() {
        val old = codec ?: return
        codec = null
        try {
            old.stop()
        } finally {
            old.release()
        }
    }

    private companion object {
        const val INPUT_TIMEOUT_US = 10_000L
    }
}
