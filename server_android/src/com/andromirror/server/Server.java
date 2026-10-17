package com.andromirror.server;

import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Build;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;

/**
 * Serveur AndroMirror (étape 2 : affichage). Lancé par le PC avec :
 *   adb shell CLASSPATH=/data/local/tmp/andromirror-server-XXXX.jar app_process / com.andromirror.server.Server clé=valeur...
 *
 * Déroulement :
 *  1. écoute sur un socket local abstrait au nom aléatoire (le PC y accède via « adb forward ») ;
 *  2. n'accepte que le client qui présente le jeton secret de 16 octets reçu en argument ;
 *  3. envoie le « bonjour » puis le flux vidéo (VideoStreamer) ;
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

            // Lecture des messages du PC ; la fin de la connexion arrête tout.
            final InputStream in = client.getInputStream();
            Thread reader = new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        while (true) {
                            int b = in.read();
                            if (b < 0 || b == Wire.C_QUIT) break;
                            if (b == Wire.C_REQUEST_KEYFRAME) vs.requestKeyframe();
                        }
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
            }
        }
    }
}
