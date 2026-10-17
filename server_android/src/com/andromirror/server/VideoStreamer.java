package com.andromirror.server;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Bundle;
import android.view.Surface;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;

/**
 * Encode l'écran avec MediaCodec (H.264 ou H.265, encodeur matériel du téléphone) et envoie les paquets au PC.
 * Quand la taille ou la rotation de l'écran change, la session d'encodage est relancée à la nouvelle taille
 * et le PC en est averti (paquet T_SIZE) pour adapter sa fenêtre.
 */
final class VideoStreamer {
    private static final long POLL_US = 50_000;           // attente d'un paquet encodé
    private static final long DISPLAY_CHECK_MS = 250;     // fréquence de vérification de la rotation

    private final Options opt;
    private final OutputStream out;
    private final Object writeLock = new Object();
    private byte[] buf = new byte[1 << 20];

    private volatile boolean stop;
    private volatile boolean keyframeRequested;

    VideoStreamer(Options opt, OutputStream out) {
        this.opt = opt;
        this.out = out;
    }

    void requestStop() {
        stop = true;
    }

    void requestKeyframe() {
        keyframeRequested = true;
    }

    private String mime() {
        return "h265".equals(opt.codec) ? MediaFormat.MIMETYPE_VIDEO_HEVC : MediaFormat.MIMETYPE_VIDEO_AVC;
    }

    // ------------------------------------------------------------------ boucle principale
    /** Boucle jusqu'à l'arrêt demandé ou une erreur. `first` : état déjà annoncé dans le « bonjour ». */
    void run(DisplayAccess.State first, int[] firstVideoSize) throws Exception {
        DisplayAccess.State state = first;
        int[] vs = firstVideoSize;
        boolean announced = true;
        while (!stop) {
            if (!announced) {
                sendSize(vs[0], vs[1], state.rotation);
            }
            announced = false;
            boolean changed = encodeSession(state, vs);
            if (!changed) break;
            state = DisplayAccess.query();
            vs = videoSize(state, opt.maxShortSide);
            Server.log("Écran modifié : " + state + " -> vidéo " + vs[0] + "x" + vs[1]);
        }
    }

    /** Taille de la vidéo : plafonnée sur le petit côté, multiples de 8, acceptée par l'encodeur. */
    int[] videoSize(DisplayAccess.State s, int maxShortSide) {
        double scale = 1.0;
        int shortSide = Math.min(s.width, s.height);
        if (maxShortSide > 0 && shortSide > maxShortSide) scale = (double) maxShortSide / shortSide;
        int w = align8((int) Math.round(s.width * scale));
        int h = align8((int) Math.round(s.height * scale));
        try {
            MediaCodecInfo.VideoCapabilities caps = capabilities();
            int guard = 0;
            while (caps != null && !caps.isSizeSupported(w, h) && guard++ < 12) {
                w = align8((int) (w * 0.85));
                h = align8((int) (h * 0.85));
            }
        } catch (Exception e) {
            Server.log("Capacités de l'encodeur illisibles : " + DisplayAccess.describe(e));
        }
        return new int[]{w, h};
    }

    static int align8(int v) {
        return Math.max(16, (v + 4) / 8 * 8);
    }

    private MediaCodecInfo.VideoCapabilities capabilities() {
        MediaCodecInfo[] infos = new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos();
        for (MediaCodecInfo info : infos) {
            if (!info.isEncoder()) continue;
            for (String t : info.getSupportedTypes()) {
                if (t.equalsIgnoreCase(mime())) {
                    return info.getCapabilitiesForType(t).getVideoCapabilities();
                }
            }
        }
        return null;
    }

    // ------------------------------------------------------------------ une session d'encodage
    /** Retourne true si l'écran a changé (relancer), false si l'arrêt est demandé. */
    private boolean encodeSession(DisplayAccess.State state, int[] vs) throws Exception {
        MediaCodec codec = null;
        Surface surface = null;
        DisplayAccess.Capture capture = null;
        try {
            Object[] made = createEncoder(vs);
            codec = (MediaCodec) made[0];
            surface = (Surface) made[1];
            int[] used = (int[]) made[2];
            if (used[0] != vs[0] || used[1] != vs[1]) {
                // l'encodeur a imposé une taille plus petite : on l'annonce
                vs[0] = used[0];
                vs[1] = used[1];
                sendSize(vs[0], vs[1], state.rotation);
            }
            capture = DisplayAccess.start(surface, state, vs[0], vs[1]);
            Server.log("Capture démarrée (" + capture.method() + ") " + vs[0] + "x" + vs[1] + " " + opt.codec
                    + " " + opt.bitrateMbps + " Mb/s " + opt.fps + " i/s");
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long lastCheck = System.currentTimeMillis();
            while (!stop) {
                if (keyframeRequested) {
                    keyframeRequested = false;
                    try {
                        Bundle b = new Bundle();
                        b.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
                        codec.setParameters(b);
                    } catch (Exception e) {
                        Server.log("Image clé non demandée : " + DisplayAccess.describe(e));
                    }
                }
                int idx = codec.dequeueOutputBuffer(info, POLL_US);
                if (idx >= 0) {
                    try {
                        if (info.size > 0) {
                            ByteBuffer data = codec.getOutputBuffer(idx);
                            if (data != null) sendVideo(data, info);
                        }
                    } finally {
                        codec.releaseOutputBuffer(idx, false);
                    }
                }
                long now = System.currentTimeMillis();
                if (now - lastCheck >= DISPLAY_CHECK_MS) {
                    lastCheck = now;
                    DisplayAccess.State cur;
                    try {
                        cur = DisplayAccess.query();
                    } catch (Exception e) {
                        continue;   // lecture ponctuelle impossible : on continue avec l'état actuel
                    }
                    if (!cur.sameAs(state)) return true;
                }
            }
            return false;
        } finally {
            if (capture != null) capture.release();
            if (codec != null) {
                try {
                    codec.stop();
                } catch (Exception ignored) {
                    // déjà arrêté
                }
                try {
                    codec.release();
                } catch (Exception ignored) {
                    // rien
                }
            }
            if (surface != null) surface.release();
        }
    }

    /**
     * Crée et démarre l'encodeur. Plusieurs essais de plus en plus prudents, car les encodeurs diffèrent
     * beaucoup selon les marques. Retourne {codec, surface, {largeur, hauteur}}.
     */
    private Object[] createEncoder(int[] vs) throws Exception {
        // essai : {profil High, paramètres de faible latence, plafond du petit côté (0 = taille demandée)}
        int[][] attempts = {{1, 1, 0}, {0, 1, 0}, {0, 0, 0}, {0, 0, 1080}, {0, 0, 720}};
        Exception last = null;
        for (int[] a : attempts) {
            int w = vs[0], h = vs[1];
            if (a[2] > 0 && Math.min(w, h) > a[2]) {
                double sc = (double) a[2] / Math.min(w, h);
                w = align8((int) Math.round(w * sc));
                h = align8((int) Math.round(h * sc));
            }
            MediaCodec codec = null;
            try {
                MediaFormat f = MediaFormat.createVideoFormat(mime(), w, h);
                f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
                f.setInteger(MediaFormat.KEY_BIT_RATE, opt.bitrateMbps * 1_000_000);
                f.setInteger(MediaFormat.KEY_FRAME_RATE, opt.fps);
                f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 5);
                if (a[0] == 1 && "h264".equals(opt.codec)) {
                    f.setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileHigh);
                }
                if (a[1] == 1) {
                    // sans changement à l'écran l'encodeur répète la dernière image (sinon le flux s'arrête)
                    f.setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 100_000L);
                    f.setInteger(MediaFormat.KEY_PRIORITY, 0);            // temps réel
                    if (Build.VERSION.SDK_INT >= 30) f.setInteger(MediaFormat.KEY_LATENCY, 1);
                }
                codec = MediaCodec.createEncoderByType(mime());
                codec.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
                Surface s = codec.createInputSurface();
                codec.start();
                if (a[0] != 1 || a[1] != 1 || a[2] != 0) {
                    Server.log("Encodeur démarré avec des réglages de secours (essai " + java.util.Arrays.toString(a) + ")");
                }
                return new Object[]{codec, s, new int[]{w, h}};
            } catch (Exception e) {
                last = e;
                Server.log("Encodeur refusé (essai " + java.util.Arrays.toString(a) + ") : " + DisplayAccess.describe(e));
                if (codec != null) {
                    try {
                        codec.release();
                    } catch (Exception ignored) {
                        // rien
                    }
                }
            }
        }
        throw new IllegalStateException("aucun encodeur " + opt.codec.toUpperCase() + " ne démarre : "
                + (last == null ? "?" : DisplayAccess.describe(last)));
    }

    // ------------------------------------------------------------------ envoi
    private void sendVideo(ByteBuffer data, MediaCodec.BufferInfo info) throws IOException {
        int flags = 0;
        if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) flags |= Wire.F_CONFIG;
        if ((info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0) flags |= Wire.F_KEY;
        synchronized (writeLock) {
            int total = Wire.HEADER_SIZE + info.size;
            if (buf.length < total) buf = new byte[Math.max(total, buf.length * 2)];
            Wire.putHeader(buf, Wire.T_VIDEO, flags, info.presentationTimeUs, info.size);
            data.position(info.offset);
            data.limit(info.offset + info.size);
            data.get(buf, Wire.HEADER_SIZE, info.size);
            out.write(buf, 0, total);
            out.flush();
        }
    }

    void sendSize(int w, int h, int rotation) throws IOException {
        byte[] payload = Wire.sizePayload(w, h, rotation);
        synchronized (writeLock) {
            out.write(Wire.header(Wire.T_SIZE, 0, 0, payload.length));
            out.write(payload);
            out.flush();
        }
    }

    void sendMessage(String text, boolean fatal) throws IOException {
        byte[] payload = text.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        synchronized (writeLock) {
            out.write(Wire.header(Wire.T_MESSAGE, fatal ? Wire.F_FATAL : 0, 0, payload.length));
            out.write(payload);
            out.flush();
        }
    }
}
