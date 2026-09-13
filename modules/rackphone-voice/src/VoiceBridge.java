import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Looper;

import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;

/** A dependency-free, one-call PSTN PCM bridge launched by app_process. */
public final class VoiceBridge {
    private static final String SOCKET_NAME = "rackphone-voice";

    private VoiceBridge() {}

    public static void main(String[] args) {
        try {
            // AudioManager builds a Handler the moment it is fetched, and a
            // Handler needs a Looper on this thread. app_process gives none, so
            // prepare one here; the bridge never runs the loop because it does
            // blocking reads and writes rather than waiting on callbacks.
            if (Looper.myLooper() == null) Looper.prepareMainLooper();
            Context context = systemContext();
            AudioManager audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            if (args.length == 1 && "--probe".equals(args[0])) {
                System.out.println("interceptable=" + (isInterceptable(audio) ? "yes" : "no"));
                return;
            }
            int requestedRate = args.length == 1 ? Integer.parseInt(args[0]) : 16000;
            run(audio, requestedRate);
        } catch (Throwable failure) {
            // Logs are operator-facing: keep every failure to one grep-friendly line.
            Throwable cause = failure instanceof InvocationTargetException
                    && failure.getCause() != null ? failure.getCause() : failure;
            System.err.println("voice bridge: " + cause.getClass().getSimpleName() + ": "
                    + String.valueOf(cause.getMessage()).replace('\n', ' '));
            System.exit(1);
        }
    }

    private static Context systemContext() throws Exception {
        // Proven on this unit: constructing ActivityThread and calling
        // getSystemContext() works under shell; systemMain() kills the process.
        Class<?> type = Class.forName("android.app.ActivityThread");
        Object thread = type.getDeclaredConstructor().newInstance();
        Method getSystemContext = type.getDeclaredMethod("getSystemContext");
        getSystemContext.setAccessible(true);
        return (Context) getSystemContext.invoke(thread);
    }

    private static boolean isInterceptable(AudioManager audio) throws Exception {
        // Proven on this unit: isPstnCallAudioInterceptable() returns true.
        Method method = AudioManager.class.getMethod("isPstnCallAudioInterceptable");
        return (Boolean) method.invoke(audio);
    }

    private static void run(AudioManager audio, int requestedRate) throws Exception {
        if (!isInterceptable(audio)) {
            throw new IllegalStateException("PSTN call audio is not interceptable");
        }
        AudioFormat format = new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                .setSampleRate(requestedRate)
                .build();

        // Proven on this unit: these @SystemApi methods are reachable only by
        // reflection, and both throw IllegalStateException unless a call is in
        // progress, so failure here prevents a stale, half-open bridge.
        Method extract = AudioManager.class.getMethod(
                "getCallDownlinkExtractionAudioRecord", AudioFormat.class);
        Method inject = AudioManager.class.getMethod(
                "getCallUplinkInjectionAudioTrack", AudioFormat.class);
        AudioRecord record = null;
        AudioTrack track = null;
        LocalServerSocket server = null;
        LocalSocket socket = null;
        try {
            record = (AudioRecord) extract.invoke(audio, format);
            track = (AudioTrack) inject.invoke(audio, format);
            int rate = record.getSampleRate();
            if (rate <= 0 || track.getSampleRate() != rate) {
                throw new IllegalStateException("audio endpoints report incompatible sample rates");
            }
            // Twenty-millisecond frames give the stream socket an unambiguous
            // unit. The endpoint-reported rate, not the requested one, sizes it;
            // (rate / 50) * 2 is always a whole number of 16-bit mono samples,
            // so no read ever splits a sample and shifts the rest to noise.
            int frameBytes = Math.max(2, (rate / 50) * 2);
            server = new LocalServerSocket(SOCKET_NAME);
            socket = server.accept();
            // Announce the real rate and frame size before any audio, exactly as
            // the screen stream announces the device before its packets: a stream
            // socket keeps no write boundaries, so both ends must agree the frame
            // size up front rather than tag each frame. One socket carries one
            // direction each way, so no per-frame channel tag is needed at all.
            writeHeader(socket.getOutputStream(), rate, frameBytes);
            record.startRecording();
            track.play();
            bridge(socket, record, track, frameBytes);
        } finally {
            // Releasing on every EOF/error matters: retained interception can
            // deny the following call access to these exclusive endpoints.
            if (socket != null) try { socket.close(); } catch (Exception ignored) {}
            if (server != null) try { server.close(); } catch (Exception ignored) {}
            if (record != null) { try { record.stop(); } catch (Exception ignored) {} record.release(); }
            if (track != null) { try { track.stop(); } catch (Exception ignored) {} track.release(); }
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
                               int frameBytes) throws Exception {
        InputStream input = socket.getInputStream();
        OutputStream output = socket.getOutputStream();
        AtomicBoolean open = new AtomicBoolean(true);
        Throwable[] failure = new Throwable[2];

        Thread downlink = new Thread(() -> {
            byte[] pcm = new byte[frameBytes];
            try {
                while (open.get()) {
                    // Fill a whole frame before sending so the far end always
                    // reads one aligned frame, never a torn one.
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
