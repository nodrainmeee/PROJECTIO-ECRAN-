package com.andromirror.server;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

/**
 * Format des messages envoyés au PC (entiers en gros-boutiste / « big endian »).
 *
 * Bonjour (une seule fois, 84 octets) :
 *   "AMV1" (4) | nom de l'appareil UTF-8 complété par des 0 (64) | codec "h264"/"h265" (4)
 *   | largeur u32 | hauteur u32 | rotation u32
 *
 * Ensuite, une suite de paquets : en-tête de 16 octets puis données.
 *   type u8 | indicateurs u8 | réservé u16 (0) | pts en microsecondes u64 | longueur u32
 *
 * Aucune dépendance à Android : cette classe est aussi testée sur un JDK de bureau.
 */
public final class Wire {
    private Wire() {}

    public static final byte[] MAGIC = {'A', 'M', 'V', '1'};
    public static final int HELLO_SIZE = 4 + 64 + 4 + 4 + 4 + 4;
    public static final int HEADER_SIZE = 16;
    public static final int NAME_SIZE = 64;

    public static final int T_VIDEO = 1;    // données encodées
    public static final int T_SIZE = 2;     // changement de taille / rotation (12 octets : largeur, hauteur, rotation)
    public static final int T_MESSAGE = 3;  // texte UTF-8 (indicateur FATAL = erreur qui arrête la session)

    public static final int F_CONFIG = 1;   // T_VIDEO : paramètres du codec (SPS/PPS...)
    public static final int F_KEY = 2;      // T_VIDEO : image clé
    public static final int F_FATAL = 1;    // T_MESSAGE : erreur fatale

    // Messages PC -> serveur (1 octet chacun)
    public static final int C_REQUEST_KEYFRAME = 1;
    public static final int C_QUIT = 2;

    public static final int TOKEN_SIZE = 16;

    public static byte[] hello(String deviceName, String codec, int width, int height, int rotation) {
        ByteBuffer b = ByteBuffer.allocate(HELLO_SIZE);
        b.put(MAGIC);
        byte[] name = deviceName.getBytes(StandardCharsets.UTF_8);
        int n = Math.min(name.length, NAME_SIZE);
        // ne pas couper un caractère UTF-8 en deux
        while (n > 0 && n < name.length && (name[n] & 0xC0) == 0x80) n--;
        b.put(name, 0, n);
        b.position(4 + NAME_SIZE);
        byte[] c = codec.getBytes(StandardCharsets.US_ASCII);
        b.put(c, 0, Math.min(4, c.length));
        b.position(4 + NAME_SIZE + 4);
        b.putInt(width).putInt(height).putInt(rotation);
        return b.array();
    }

    /** Écrit l'en-tête dans buf à partir de l'offset 0. */
    public static void putHeader(byte[] buf, int type, int flags, long ptsUs, int length) {
        ByteBuffer b = ByteBuffer.wrap(buf, 0, HEADER_SIZE);
        b.put((byte) type).put((byte) flags).putShort((short) 0).putLong(ptsUs).putInt(length);
    }

    public static byte[] header(int type, int flags, long ptsUs, int length) {
        byte[] h = new byte[HEADER_SIZE];
        putHeader(h, type, flags, ptsUs, length);
        return h;
    }

    public static byte[] sizePayload(int width, int height, int rotation) {
        return ByteBuffer.allocate(12).putInt(width).putInt(height).putInt(rotation).array();
    }
}
