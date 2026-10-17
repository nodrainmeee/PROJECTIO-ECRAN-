package com.andromirror.server;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.os.Build;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

/**
 * Capture le son interne du téléphone (sans micro) et l'envoie au PC en PCM 16 bits, 48 kHz, stéréo.
 *
 * Source utilisée : REMOTE_SUBMIX (8). Android détourne alors le son vers cette capture au lieu du haut-parleur :
 * c'est ce qui rend le téléphone muet pendant le transfert. Disponible à partir d'Android 11 ; les applications
 * peuvent refuser d'être capturées (leur son ne sera alors pas transmis).
 *
 * API cachées (réflexion) : AudioRecord.Builder.setContext (Android 12+). Sur Android 11 il faut en plus que
 * l'application « shell » soit au premier plan : une activité vide est démarrée un instant puis arrêtée.
 */
final class AudioStreamer {
    static final int SAMPLE_RATE = 48000;
    static final int CHANNELS = 2;
    private static final int REMOTE_SUBMIX = 8;                 // MediaRecorder.AudioSource.REMOTE_SUBMIX (caché)
    private static final int CHUNK_BYTES = 960 * CHANNELS * 2;  // 20 ms

    private final VideoStreamer out;
    private volatile boolean stop;
    private AudioRecord record;
    private Thread thread;

    AudioStreamer(VideoStreamer out) {
        this.out = out;
    }

    /** Démarre la capture. Lève une exception expliquant l'échec (le PC l'affiche ; l'image continue). */
    void start() throws Exception {
        if (Build.VERSION.SDK_INT < 30) {
            throw new IllegalStateException("le transfert du son demande Android 11 ou plus récent (ce téléphone : "
                    + "Android " + Build.VERSION.RELEASE + ")");
        }
        boolean android11 = Build.VERSION.SDK_INT == 30;
        if (android11) shell("am", "start", "-n", FakeContext.PACKAGE + "/.HeapDumpActivity");
        try {
            record = createRecord();
            record.startRecording();
        } finally {
            if (android11) shell("am", "force-stop", FakeContext.PACKAGE);
        }
        if (record.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
            release();
            throw new IllegalStateException("la capture du son n'a pas démarré");
        }
        out.sendPacket(Wire.T_AUDIO, Wire.F_CONFIG, 0, Wire.audioConfig(SAMPLE_RATE, CHANNELS, 16), 8);
        Server.log("Son : capture démarrée (" + SAMPLE_RATE + " Hz, stéréo)");
        thread = new Thread(new Runnable() {
            @Override
            public void run() {
                loop();
            }
        }, "audio");
        thread.setDaemon(true);
        thread.start();
    }

    private AudioRecord createRecord() throws Exception {
        AudioRecord.Builder b = new AudioRecord.Builder();
        if (Build.VERSION.SDK_INT >= 31) {
            try {
                b.getClass().getMethod("setContext", Context.class).invoke(b, FakeContext.get());
            } catch (Exception e) {
                Server.log("Son : contexte non appliqué : " + DisplayAccess.describe(e));
            }
        } else {
            FakeContext.get();     // Android 11 : prépare l'identité « shell » (voir FakeContext)
        }
        b.setAudioSource(REMOTE_SUBMIX);
        b.setAudioFormat(new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(SAMPLE_RATE)
                .setChannelMask(AudioFormat.CHANNEL_IN_STEREO)
                .build());
        int min = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_STEREO,
                AudioFormat.ENCODING_PCM_16BIT);
        b.setBufferSizeInBytes(Math.max(min, CHUNK_BYTES * 8));
        return b.build();
    }

    private void loop() {
        byte[] buf = new byte[CHUNK_BYTES];
        long t0 = System.nanoTime();
        try {
            while (!stop) {
                int n = record.read(buf, 0, buf.length);
                if (n < 0) {
                    Server.log("Son : lecture impossible (code " + n + ")");
                    notifyPc("Le son du téléphone s'est arrêté (erreur " + n + ").");
                    return;
                }
                if (n > 0) out.sendPacket(Wire.T_AUDIO, 0, (System.nanoTime() - t0) / 1000L, buf, n);
            }
        } catch (IOException pcGone) {
            // le PC est parti : fin normale
        } catch (Throwable t) {
            Server.log("Son : erreur : " + DisplayAccess.describe(t));
            notifyPc("Le son du téléphone s'est arrêté : " + DisplayAccess.describe(t));
        }
    }

    private void notifyPc(String text) {
        try {
            out.sendMessage(text, false);
        } catch (IOException ignored) {
            // le PC est parti
        }
    }

    void stop() {
        stop = true;
        AudioRecord r = record;
        if (r != null) {
            try {
                r.stop();                 // débloque une lecture en attente
            } catch (Exception ignored) {
                // déjà arrêté
            }
        }
        Thread t = thread;
        if (t != null) {
            try {
                t.join(500);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        release();
    }

    private void release() {
        AudioRecord r = record;
        record = null;
        if (r != null) {
            try {
                r.release();
            } catch (Exception ignored) {
                // rien
            }
        }
    }

    /** Lance une commande système (am ...) et attend sa fin quelques secondes. Les erreurs sont seulement journalisées. */
    private static void shell(String... cmd) {
        try {
            Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
            if (!p.waitFor(4, TimeUnit.SECONDS)) p.destroy();
        } catch (Exception e) {
            Server.log("Son : commande « " + cmd[0] + " » impossible : " + DisplayAccess.describe(e));
        }
    }
}
