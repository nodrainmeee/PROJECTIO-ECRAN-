package com.andromirror.server;

import java.util.HashMap;
import java.util.Map;

/** Arguments « clé=valeur » donnés par le PC au lancement. Valeurs invalides = valeurs par défaut sûres. */
final class Options {
    final byte[] token;          // 16 octets : seul le PC qui le connaît peut se connecter
    final String socketName;     // nom du socket local abstrait
    final String codec;          // "h264" ou "h265"
    final int bitrateMbps;       // 4..60
    final int fps;               // 15..120
    final int maxShortSide;      // 0 = résolution native, sinon plafond du petit côté (720, 1080, 1440, 2160)
    final String jarPath;        // fichier à supprimer en fin de session ("" = rien)
    final int acceptTimeoutSec;  // attente de la connexion du PC
    final boolean audio;         // envoyer aussi le son du téléphone (Android 11 ou plus)

    private Options(Map<String, String> m) {
        token = parseHex(m.get("token"), Wire.TOKEN_SIZE);
        String sock = m.get("socket");
        if (sock == null || !sock.matches("[A-Za-z0-9_.-]{1,64}")) {
            throw new IllegalArgumentException("socket invalide");
        }
        socketName = sock;
        codec = "h265".equals(m.get("codec")) ? "h265" : "h264";
        bitrateMbps = clamp(parseInt(m.get("bitrate"), 16), 4, 60);
        fps = clamp(parseInt(m.get("fps"), 60), 15, 120);
        maxShortSide = Math.max(0, parseInt(m.get("max_short"), 0));
        String jar = m.get("jar");
        // on ne supprime que notre propre fichier, jamais autre chose
        jarPath = (jar != null && jar.startsWith("/data/local/tmp/andromirror-server")) && !jar.contains("..") ? jar : "";
        acceptTimeoutSec = clamp(parseInt(m.get("accept_timeout"), 30), 3, 300);
        audio = "1".equals(m.get("audio"));
    }

    static Options parse(String[] args) {
        Map<String, String> m = new HashMap<>();
        for (String a : args) {
            int i = a.indexOf('=');
            if (i > 0) m.put(a.substring(0, i), a.substring(i + 1));
        }
        return new Options(m);
    }

    private static int parseInt(String s, int dflt) {
        try {
            return s == null ? dflt : Integer.parseInt(s.trim());
        } catch (NumberFormatException e) {
            return dflt;
        }
    }

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    private static byte[] parseHex(String s, int bytes) {
        if (s == null || s.length() != bytes * 2) throw new IllegalArgumentException("token invalide");
        byte[] out = new byte[bytes];
        for (int i = 0; i < bytes; i++) {
            int hi = Character.digit(s.charAt(2 * i), 16);
            int lo = Character.digit(s.charAt(2 * i + 1), 16);
            if (hi < 0 || lo < 0) throw new IllegalArgumentException("token invalide");
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }
}
