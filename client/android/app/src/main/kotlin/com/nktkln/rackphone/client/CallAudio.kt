package com.nktkln.rackphone.client

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import io.flutter.plugin.common.BasicMessageChannel
import java.nio.ByteBuffer
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max

/** Owns one low-latency, voice-optimized Android playback/capture pair. */
class CallAudio(
    private val activity: Activity,
    private val channel: BasicMessageChannel<ByteBuffer>,
) {
    private val executor = Executors.newFixedThreadPool(2)
    private val main = Handler(Looper.getMainLooper())
    private val running = AtomicBoolean(false)
    private var track: AudioTrack? = null
    private var record: AudioRecord? = null
    private var pending: PendingStart? = null

    private data class PendingStart(
        val sampleRate: Int,
        val frameBytes: Int,
        val reply: BasicMessageChannel.Reply<ByteBuffer>,
    )

    /** Requests microphone permission only when the operator accepts a call. */
    fun start(sampleRate: Int, frameBytes: Int, reply: BasicMessageChannel.Reply<ByteBuffer>) {
        require(sampleRate > 0 && frameBytes > 0) { "Audio format must be positive" }
        if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            pending = PendingStart(sampleRate, frameBytes, reply)
            activity.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), PERMISSION_REQUEST)
            return
        }
        create(sampleRate, frameBytes)
        reply.reply(status(true))
    }

    fun onRequestPermissionsResult(requestCode: Int, grantResults: IntArray): Boolean {
        if (requestCode != PERMISSION_REQUEST) return false
        val request = pending ?: return true
        pending = null
        val granted = grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        if (granted) create(request.sampleRate, request.frameBytes)
        request.reply.reply(status(granted))
        return true
    }

    /** Copies a Dart-owned buffer before playing it away from the platform thread. */
    fun play(bytes: ByteArray) {
        executor.execute { track?.write(bytes, 0, bytes.size, AudioTrack.WRITE_BLOCKING) }
    }

    /** Stops both blocking native loops; repeated disposal is harmless. */
    fun dispose() {
        running.set(false)
        record?.stopSafely()
        track?.pause()
        record?.release()
        track?.release()
        record = null
        track = null
    }

    fun close() {
        dispose()
        executor.shutdownNow()
    }

    // Reached only with RECORD_AUDIO granted: start checks it, and the permission
    // result calls this only on a grant. Lint cannot follow either across calls.
    @SuppressLint("MissingPermission")
    private fun create(sampleRate: Int, frameBytes: Int) {
        dispose()
        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(sampleRate)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        val trackBuffer = max(
            frameBytes * 2,
            AudioTrack.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            ),
        )
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(format)
            .setBufferSizeInBytes(trackBuffer)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        val recordBuffer = max(
            frameBytes * 2,
            AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            ),
        )
        record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            recordBuffer,
        )
        check(record?.state == AudioRecord.STATE_INITIALIZED) { "AudioRecord initialization failed" }
        running.set(true)
        track!!.play()
        record!!.startRecording()
        executor.execute { capture(frameBytes) }
    }

    private fun capture(frameBytes: Int) {
        val frame = ByteArray(frameBytes)
        var filled = 0
        while (running.get()) {
            val count = record?.read(frame, filled, frameBytes - filled, AudioRecord.READ_BLOCKING) ?: break
            if (count <= 0) continue
            filled += count
            if (filled == frameBytes) {
                // Platform-channel sends must run on the main thread, not this
                // capture loop; copy the frame out before handing it over.
                val outgoing = frame.copyOf()
                main.post { channel.send(ByteBuffer.wrap(outgoing)) }
                filled = 0
            }
        }
    }

    private fun AudioRecord.stopSafely() {
        try {
            stop()
        } catch (_: IllegalStateException) {
            // Already stopped is the desired teardown state.
        }
    }

    private fun status(ok: Boolean): ByteBuffer =
        ByteBuffer.allocateDirect(1).put((if (ok) 1 else 0).toByte()).also { it.flip() }

    companion object {
        const val PERMISSION_REQUEST = 8174
    }
}
