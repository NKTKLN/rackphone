import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Looper;

import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * A dependency-free, one-call two-way PSTN audio bridge launched by app_process.
 *
 * The Android call-audio interception @SystemApi is a dead end on this device:
 * getCallUplinkInjectionAudioTrack drains and routes to TYPE_TELEPHONY, yet the
 * far party hears nothing. What works instead, proven live on this unit, is the
 * vendor in-call-music path:
 *   downlink  = AudioRecord(VOICE_DOWNLINK)          - the far party's voice.
 *   uplink    = AudioTrack(USAGE_MEDIA) mixed into   - what the far party hears,
 *               the call by the Qualcomm mixer          enabled with tinymix.
 *               "Incall_Music Audio Mixer MultiMediaN".
 * Both run in the ordinary in-call mode; no setMode, no reflection, no context.
 */
public final class VoiceBridge {
    private static final String DEFAULT_SOCKET = "rackphone-voice";
    private static final String SOCKET_FLAG = "--socket=";
    // Who may take the call: adbd, which runs as shell and carries the host's
    // forward, or root. Any other uid is an app on the unit that got there first.
    private static final int ROOT_UID = 0;
    private static final int SHELL_UID = 2000;
    private static final int DEFAULT_RATE = 48000;

    // The media stream lands on one of these front-ends; which one is not
    // deterministic, so every candidate route into the call uplink is opened.
    // Controls are addressed by name because their numeric ids shift per boot.
    private static final String[] MIXER_CONTROLS = {
        "Incall_Music Audio Mixer MultiMedia1",
        "Incall_Music Audio Mixer MultiMedia2",
        "Incall_Music Audio Mixer MultiMedia4",
        "Incall_Music Audio Mixer MultiMedia5",
        "Incall_Music Audio Mixer MultiMedia9",
        "Incall_Music_2 Audio Mixer MultiMedia1",
        "Incall_Music_2 Audio Mixer MultiMedia2",
        "Incall_Music_2 Audio Mixer MultiMedia5",
        "Incall_Music_2 Audio Mixer MultiMedia9",
    };

    private VoiceBridge() {}

    public static void main(String[] args) {
        try {
            // AudioRecord/AudioTrack construct their own Handlers, which need a
            // Looper on this thread that app_process does not provide.
            if (Looper.myLooper() == null) Looper.prepareMainLooper();
            int rate = DEFAULT_RATE;
            boolean probe = false;
            String socketName = DEFAULT_SOCKET;
            for (String arg : args) {
                if ("--probe".equals(arg)) probe = true;
                else if (arg.startsWith(SOCKET_FLAG)) socketName = arg.substring(SOCKET_FLAG.length());
                else rate = Integer.parseInt(arg);
            }
            if (probe) {
                probe(rate);
                return;
            }
            run(rate, socketName);
        } catch (Throwable failure) {
            // Logs are operator-facing: keep every failure to one grep-friendly line.
            System.err.println("voice bridge: " + failure.getClass().getSimpleName() + ": "
                    + String.valueOf(failure.getMessage()).replace('\n', ' '));
            System.exit(1);
        }
    }

    private static void probe(int rate) {
        // Report readiness without a call: both endpoints construct in any mode,
        // so this proves the audio path is reachable before an operator relies
        // on it. The mixer is a tinymix control, checked separately in status.
        AudioRecord record = null;
        AudioTrack track = null;
        try {
            record = openDownlink(rate);
            track = openUplink(rate);
            boolean ready = record.getState() == AudioRecord.STATE_INITIALIZED
                    && track.getState() == AudioTrack.STATE_INITIALIZED;
            System.out.println("ready=" + (ready ? "yes" : "no"));
        } catch (Throwable failure) {
            System.out.println("ready=no");
        } finally {
            if (record != null) record.release();
            if (track != null) track.release();
        }
    }

    private static AudioRecord openDownlink(int rate) {
        int size = Math.max(rate, AudioRecord.getMinBufferSize(
                rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT));
        AudioRecord record = new AudioRecord(MediaRecorder.AudioSource.VOICE_DOWNLINK,
                rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size);
        if (record.getState() != AudioRecord.STATE_INITIALIZED) {
            // Some builds only expose the mixed call source; the far party still
            // dominates it, so it is the right fallback for hearing them.
            record.release();
            record = new AudioRecord(MediaRecorder.AudioSource.VOICE_CALL,
                    rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size);
        }
        return record;
    }

    private static AudioTrack openUplink(int rate) {
        AudioAttributes attributes = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build();
        AudioFormat format = new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .setSampleRate(rate)
                .build();
        int size = Math.max(rate, AudioTrack.getMinBufferSize(
                rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT));
        return new AudioTrack(attributes, format, size, AudioTrack.MODE_STREAM, 0);
    }

    private static void run(int rate, String socketName) throws Exception {
        AudioRecord record = openDownlink(rate);
        AudioTrack track = openUplink(rate);
        LocalServerSocket server = null;
        LocalSocket socket = null;
        AtomicBoolean open = new AtomicBoolean(true);
        Thread mixer = null;
        try {
            if (record.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("downlink capture did not initialize");
            }
            // (rate / 50) * 2 is a 20 ms frame, always a whole number of 16-bit
            // mono samples, so no socket read ever splits a sample.
            int frameBytes = Math.max(2, (rate / 50) * 2);
            server = new LocalServerSocket(socketName);
            socket = acceptHost(server);
            // Announce the rate and frame size before any audio, as the screen
            // stream announces the device: a stream socket keeps no write
            // boundaries, so both ends must agree the frame size up front.
            writeHeader(socket.getOutputStream(), rate, frameBytes);
            mixer = startMixer(open);
            record.startRecording();
            track.play();
            bridge(socket, record, track, frameBytes, open);
        } finally {
            open.set(false);
            if (mixer != null) mixer.interrupt();
            if (socket != null) try { socket.close(); } catch (Exception ignored) {}
            if (server != null) try { server.close(); } catch (Exception ignored) {}
            try { record.stop(); } catch (Exception ignored) {}
            record.release();
            try { track.stop(); } catch (Exception ignored) {}
            track.release();
            // Leaving the mixer routed would feed later media into the next call.
            setMixers(false);
        }
    }

    /**
     * Waits for the host's connection, turning away anyone else.
     *
     * An abstract socket has no file permissions, so its name is all that keeps
     * an app out, and an app that learns it could take the one accept and with
     * it both sides of the call.
     */
    private static LocalSocket acceptHost(LocalServerSocket server) throws Exception {
        while (true) {
            LocalSocket peer = server.accept();
            int uid = peer.getPeerCredentials().getUid();
            if (uid == ROOT_UID || uid == SHELL_UID) return peer;
            System.err.println("voice bridge: refused a connection from uid " + uid);
            try { peer.close(); } catch (Exception ignored) {}
        }
    }

    /** Keeps the in-call-music routes enabled for as long as the bridge runs. */
    private static Thread startMixer(AtomicBoolean open) {
        Thread thread = new Thread(() -> {
            while (open.get()) {
                setMixers(true);
                try { Thread.sleep(1500); } catch (InterruptedException e) { return; }
            }
        }, "voice-mixer");
        thread.setDaemon(true);
        thread.start();
        return thread;
    }

    private static void setMixers(boolean on) {
        String value = on ? "1" : "0";
        for (String control : MIXER_CONTROLS) {
            try {
                Process p = new ProcessBuilder("/system/bin/tinymix", control, value)
                        .redirectErrorStream(true).start();
                p.getInputStream().close();
                p.waitFor();
            } catch (Exception ignored) {
                // A missing control on a different build is not fatal; the point
                // is to open whichever of the candidates this device exposes.
            }
        }
    }

    private static void writeHeader(OutputStream output, int rate, int frameBytes) throws Exception {
        byte[] header = new byte[8];
        putInt(header, 0, rate);
        putInt(header, 4, frameBytes);
        output.write(header);
        output.flush();
    }

    private static void putInt(byte[] bytes, int offset, int value) {
        bytes[offset] = (byte) (value >>> 24);
        bytes[offset + 1] = (byte) (value >>> 16);
        bytes[offset + 2] = (byte) (value >>> 8);
        bytes[offset + 3] = (byte) value;
    }

    private static void bridge(LocalSocket socket, AudioRecord record, AudioTrack track,
                               int frameBytes, AtomicBoolean open) throws Exception {
        InputStream input = socket.getInputStream();
        OutputStream output = socket.getOutputStream();
        Throwable[] failure = new Throwable[2];

        Thread downlink = new Thread(() -> {
            byte[] pcm = new byte[frameBytes];
            try {
                while (open.get()) {
                    int offset = 0;
                    while (offset < frameBytes) {
                        int count = record.read(pcm, offset, frameBytes - offset);
                        if (count <= 0) throw new IllegalStateException("downlink ended: " + count);
                        offset += count;
                    }
                    output.write(pcm, 0, frameBytes);
                    output.flush();
                }
            } catch (Throwable t) { failure[0] = t; }
            finally { open.set(false); try { socket.close(); } catch (Exception ignored) {} }
        }, "voice-downlink");
        Thread uplink = new Thread(() -> {
            byte[] pcm = new byte[frameBytes];
            try {
                while (open.get()) {
                    if (!readFully(input, pcm)) break;
                    int written = track.write(pcm, 0, pcm.length);
                    if (written != pcm.length) throw new IllegalStateException("uplink ended: " + written);
                }
            } catch (Throwable t) { failure[1] = t; }
            finally { open.set(false); try { socket.close(); } catch (Exception ignored) {} }
        }, "voice-uplink");
        downlink.start();
        uplink.start();
        downlink.join();
        uplink.join();
        if (failure[0] != null && !(failure[0] instanceof java.io.IOException)) throwAsException(failure[0]);
        if (failure[1] != null && !(failure[1] instanceof java.io.IOException)) throwAsException(failure[1]);
    }

    /** Reads one full frame; returns false on a clean end-of-stream. */
    private static boolean readFully(InputStream input, byte[] bytes) throws Exception {
        int offset = 0;
        while (offset < bytes.length) {
            int count = input.read(bytes, offset, bytes.length - offset);
            if (count < 0) {
                if (offset == 0) return false;
                throw new java.io.EOFException("socket closed mid-frame");
            }
            offset += count;
        }
        return true;
    }

    private static void throwAsException(Throwable failure) throws Exception {
        if (failure instanceof Exception) throw (Exception) failure;
        throw new RuntimeException(failure);
    }
}
