import com.andromirror.server.Wire;

/** Affiche en hexadécimal les messages produits par Wire, pour les comparer au côté Python. */
public class WireSelfTest {
    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 0xff));
        return s.toString();
    }

    public static void main(String[] a) {
        System.out.println("HELLO " + hex(Wire.hello("Pixel é", "h264", 1080, 2400, 1)));
        System.out.println("HEADER " + hex(Wire.header(Wire.T_VIDEO, Wire.F_KEY, 123456789L, 4242)));
        System.out.println("SIZE " + hex(Wire.sizePayload(2400, 1080, 3)));
        StringBuilder longName = new StringBuilder();
        for (int i = 0; i < 70; i++) longName.append('é');
        System.out.println("AUDIOCFG " + hex(Wire.audioConfig(48000, 2, 16)));
        System.out.println("AUDIOHEADER " + hex(Wire.header(Wire.T_AUDIO, Wire.F_CONFIG, 0, 8)));
        System.out.println("LONGNAME " + hex(Wire.hello(longName.toString(), "h265", 1, 2, 0)));
    }
}
