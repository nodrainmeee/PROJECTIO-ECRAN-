package com.andromirror.server;

import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Build;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Serveur AndroMirror (affichage, contrôle depuis le PC, son). Lancé par le PC avec :
 *   adb shell CLASSPATH=/data/local/tmp/andromirror-server-XXXX.jar app_process / com.andromirror.server.Server clé=valeur...
 *
 * Déroulement :
 *  1. écoute sur un socket local abstrait au nom aléatoire (le PC y accède via « adb forward ») ;
 *  2. n'accepte que le client qui présente le jeton secret de 16 octets reçu en argument ;
 *  3. envoie le « bonjour » puis le flux vidéo (VideoStreamer) et, si demandé, le son (AudioStreamer) ;
 *     exécute les commandes de contrôle du PC (InputInjector) ;
 *  4. s'arrête dès que le PC se déconnecte (ou sur erreur) et supprime son propre fichier .jar.
 *
 * Aucune donnée ne quitte le téléphone autrement que par ce socket local (donc par le câble USB vers le PC).
 */
public final class Server {
    private Server() {}

    static void log(String s) {
        System.out.println("[serveur] " + s);
        System.out.flush();
    }

    public static void main(String[] args) {
        int code = 1;
        Options opt = null;
        try {
            opt = Options.parse(args);
            code = new Session(opt).run();
        } catch (IllegalArgumentException e) {
            log("arguments invalides : " + e.getMessage());
        } catch (Throwable t) {
            log("erreur : " + DisplayAccess.describe(t));
        } finally {
            if (opt != null) deleteSelf(opt);
        }
        log("fin (code " + code + ")");
        System.exit(code);
    }

    private static void deleteSelf(Options opt) {
        if (opt.jarPath.isEmpty()) return;
        try {
            File f = new File(opt.jarPath);
            if (f.exists() && !f.delete()) log("impossible de supprimer " + opt.jarPath);
        } catch (Exception e) {
            log("suppression : " + DisplayAccess.describe(e));
        }
    }

    private static final class Session {
        private final Options opt;
        private volatile VideoStreamer streamer;

        Session(Options opt) {
            this.opt = opt;
        }

        int run() throws Exception {
            log("démarrage, Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + "), "
                    + Build.MANUFACTURER + " " + Build.MODEL);
            LocalServerSocket server = new LocalServerSocket(opt.socketName);
            LocalSocket client;
            try {
                client = acceptAuthorized(server);
            } finally {
                try {
                    server.close();   // un seul client : le socket n'a plus de raison d'écouter
                } catch (IOException ignored) {
                    // rien
                }
            }
            if (client == null) {
                log("aucun client autorisé dans le délai imparti");
                return 2;
            }
            try {
                return stream(client);
            } finally {
                try {
                    client.close();
                } catch (IOException ignored) {
                    // rien
                }
            }
        }

        /** Attend un client qui présente le bon jeton ; refuse les autres. null si le délai est dépassé. */
        private LocalSocket acceptAuthorized(final LocalServerSocket server) throws IOException {
            final long deadline = System.currentTimeMillis() + opt.acceptTimeoutSec * 1000L;
            Thread watchdog = new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        Thread.sleep(opt.acceptTimeoutSec * 1000L);
                        server.close();   // débloque accept()
                    } catch (Exception ignored) {
                        // interrompu : un client est arrivé
                    }
                }
            }, "watchdog");
            watchdog.setDaemon(true);
            watchdog.start();
            try {
                while (System.currentTimeMillis() < deadline) {
                    LocalSocket c;
                    try {
                        c = server.accept();
                    } catch (IOException closed) {
                        return null;
                    }
                    if (checkToken(c)) return c;
                    log("connexion refusée (jeton invalide)");
                    try {
                        c.close();
                    } catch (IOException ignored) {
                        // rien
                    }
                }
                return null;
            } finally {
                watchdog.interrupt();
            }
        }

        private boolean checkToken(LocalSocket c) {
            try {
                c.setSoTimeout(5000);
                InputStream in = c.getInputStream();
                byte[] got = new byte[Wire.TOKEN_SIZE];
                int n = 0;
                while (n < got.length) {
                    int r = in.read(got, n, got.length - n);
                    if (r < 0) return false;
                    n += r;
                }
                c.setSoTimeout(0);
                return MessageDigest.isEqual(got, opt.token);   // comparaison à durée constante
            } catch (IOException e) {
                return false;
            }
        }

        private int stream(LocalSocket client) throws Exception {
            OutputStream out = client.getOutputStream();
            final VideoStreamer vs = new VideoStreamer(opt, out);
            streamer = vs;

            DisplayAccess.State state = DisplayAccess.query();
            int[] size = vs.videoSize(state, opt.maxShortSide);
            String name = (Build.MANUFACTURER + " " + Build.MODEL).trim();
            out.write(Wire.hello(name, opt.codec, size[0], size[1], state.rotation));
            out.flush();
            log("client connecté ; écran " + state + " -> vidéo " + size[0] + "x" + size[1]);

            // Contrôle depuis le PC : si le système refuse, l'image continue et le PC en est averti.
            InputInjector injector = null;
            try {
                injector = InputInjector.create(vs);
            } catch (Throwable t) {
                String msg = "Le contrôle depuis le PC n'est pas disponible sur ce téléphone : " + DisplayAccess.describe(t);
                log(msg);
                vs.sendMessage(msg, false);
            }
            final InputInjector inj = injector;

            // Son : idem, une panne du son ne coupe jamais l'image.
            AudioStreamer audioStreamer = null;
            if (opt.audio) {
                try {
                    AudioStreamer as = new AudioStreamer(vs);
                    as.start();
                    audioStreamer = as;
                } catch (Throwable t) {
                    String msg = "Le son du téléphone n'a pas pu démarrer : " + DisplayAccess.describe(t);
                    log(msg);
                    vs.sendMessage(msg, false);
                }
            }
            final AudioStreamer audio = audioStreamer;

            // Lecture des commandes du PC ; la fin de la connexion arrête tout.
            final DataInputStream in = new DataInputStream(new BufferedInputStream(client.getInputStream()));
            Thread reader = new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        readCommands(in, vs, inj);
                    } catch (IOException ignored) {
                        // connexion coupée
                    }
                    log("le PC s'est déconnecté");
                    vs.requestStop();
                }
            }, "lecture-pc");
            reader.setDaemon(true);
            reader.start();

            try {
                vs.run(state, size);
                return 0;
            } catch (IOException e) {
                log("connexion perdue : " + DisplayAccess.describe(e));
                return 0;   // le PC n'est plus là : fin normale
            } catch (Throwable t) {
                String msg = "Le serveur du téléphone a rencontré une erreur : " + DisplayAccess.describe(t);
                log(msg);
                try {
                    vs.sendMessage(msg, true);
                } catch (IOException ignored) {
                    // le PC est parti
                }
                return 1;
            } finally {
                vs.requestStop();
                if (audio != null) audio.stop();
            }
        }

        /** Boucle de lecture : un octet de type, puis le corps de la commande. Une commande inconnue arrête la lecture. */
        private void readCommands(DataInputStream in, VideoStreamer vs, InputInjector inj) throws IOException {
            while (true) {
                int type = in.read();
                if (type < 0 || type == Wire.C_QUIT) return;
                switch (type) {
                    case Wire.C_REQUEST_KEYFRAME:
                        vs.requestKeyframe();
                        break;
                    case Wire.C_TOUCH: {
                        int action = in.readUnsignedByte();
                        int x = in.readUnsignedShort();
                        int y = in.readUnsignedShort();
                        int w = in.readUnsignedShort();
                        int h = in.readUnsignedShort();
                        if (inj != null) inj.touch(action, x, y, w, h);
                        break;
                    }
                    case Wire.C_SCROLL: {
                        int x = in.readUnsignedShort();
                        int y = in.readUnsignedShort();
                        int w = in.readUnsignedShort();
                        int h = in.readUnsignedShort();
                        int hs = in.readShort();
                        int vsc = in.readShort();
                        if (inj != null) inj.scroll(x, y, w, h, hs, vsc);
                        break;
                    }
                    case Wire.C_KEY: {
                        int action = in.readUnsignedByte();
                        int code = in.readInt();
                        int meta = in.readInt();
                        if (inj != null) inj.key(action, code, meta);
                        break;
                    }
                    case Wire.C_TEXT: {
                        int len = in.readUnsignedShort();
                        if (len > Wire.MAX_TEXT_BYTES) {
                            log("texte trop long (" + len + " octets) : lecture arrêtée");
                            return;
                        }
                        byte[] raw = new byte[len];
                        in.readFully(raw);
                        if (inj != null) inj.text(new String(raw, StandardCharsets.UTF_8));
                        break;
                    }
                    default:
                        log("commande inconnue (" + type + ") : lecture arrêtée");
                        return;
                }
            }
        }
    }
}
