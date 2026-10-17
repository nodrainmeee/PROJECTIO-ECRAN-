package com.andromirror.server;

import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import android.view.MotionEvent;

import java.lang.reflect.Method;

/**
 * Injecte les actions du PC (toucher, molette, touches, texte) dans le téléphone.
 *
 * L'identité « shell » (uid 2000) a le droit d'injecter des événements d'entrée. La méthode
 * InputManager.injectInputEvent est une API CACHÉE : elle est appelée par réflexion, et son absence est signalée
 * clairement (le PC affiche alors que le contrôle est indisponible ; l'image continue).
 *
 * Les coordonnées reçues sont en pixels de la VIDÉO ; on les convertit en pixels de l'écran réel. Si la taille de
 * la vidéo annoncée par le PC n'est plus la taille actuelle (rotation en cours), l'événement est ignoré.
 */
final class InputInjector {
    static final int ACTION_DOWN = 0;
    static final int ACTION_UP = 1;
    static final int ACTION_MOVE = 2;

    private static final int INJECT_MODE_ASYNC = 0;
    private static final int ACTION_SCROLL = 8;       // MotionEvent.ACTION_SCROLL
    private static final int AXIS_VSCROLL = 9;        // MotionEvent.AXIS_VSCROLL
    private static final int AXIS_HSCROLL = 10;       // MotionEvent.AXIS_HSCROLL

    private final VideoStreamer video;
    private final Object inputManager;
    private final Method inject;

    private long downTime;
    private boolean touching;
    private boolean errorLogged;
    private long lastDropLog;

    private InputInjector(VideoStreamer video, Object inputManager, Method inject) {
        this.video = video;
        this.inputManager = inputManager;
        this.inject = inject;
    }

    /** Trouve le gestionnaire d'entrée du système. Lève une exception expliquant l'échec si aucune méthode ne marche. */
    static InputInjector create(VideoStreamer video) throws Exception {
        String[] holders = {"android.hardware.input.InputManager", "android.hardware.input.InputManagerGlobal"};
        Exception last = null;
        for (String name : holders) {
            try {
                Class<?> c = Class.forName(name);
                Method getInstance = c.getDeclaredMethod("getInstance");
                getInstance.setAccessible(true);
                Object manager = getInstance.invoke(null);
                if (manager == null) throw new IllegalStateException(name + ".getInstance() a renvoyé null");
                Method m = manager.getClass().getMethod("injectInputEvent", InputEvent.class, int.class);
                m.setAccessible(true);
                return new InputInjector(video, manager, m);
            } catch (Exception e) {
                last = e;
                Server.log("Contrôle : " + name + " inutilisable : " + DisplayAccess.describe(e));
            }
        }
        throw new IllegalStateException(last == null ? "aucun gestionnaire d'entrée" : DisplayAccess.describe(last));
    }

    // ------------------------------------------------------------------ toucher
    synchronized void touch(int action, int x, int y, int videoW, int videoH) {
        float[] p = toDisplay(x, y, videoW, videoH);
        if (p == null) return;
        long now = SystemClock.uptimeMillis();
        if (action == ACTION_DOWN) {
            if (touching) send(motion(now, ACTION_UP, p[0], p[1], 0f));   // un appui précédent est resté ouvert
            downTime = now;
            touching = true;
        } else if (!touching) {
            return;                                                     // déplacement ou relâchement sans appui
        }
        int a = action == ACTION_UP ? MotionEvent.ACTION_UP : action == ACTION_MOVE ? MotionEvent.ACTION_MOVE
                : MotionEvent.ACTION_DOWN;
        send(motion(now, a, p[0], p[1], action == ACTION_UP ? 0f : 1f));
        if (action == ACTION_UP) touching = false;
    }

    private MotionEvent motion(long now, int action, float x, float y, float pressure) {
        MotionEvent.PointerProperties[] props = {new MotionEvent.PointerProperties()};
        props[0].id = 0;
        props[0].toolType = MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords[] coords = {new MotionEvent.PointerCoords()};
        coords[0].x = x;
        coords[0].y = y;
        coords[0].pressure = pressure;
        coords[0].size = 1f;
        return MotionEvent.obtain(downTime, now, action, 1, props, coords, 0, 0, 1f, 1f, 0, 0,
                InputDevice.SOURCE_TOUCHSCREEN, 0);
    }

    // ------------------------------------------------------------------ molette
    synchronized void scroll(int x, int y, int videoW, int videoH, int hUnits, int vUnits) {
        float[] p = toDisplay(x, y, videoW, videoH);
        if (p == null) return;
        long now = SystemClock.uptimeMillis();
        MotionEvent.PointerProperties[] props = {new MotionEvent.PointerProperties()};
        props[0].id = 0;
        props[0].toolType = MotionEvent.TOOL_TYPE_MOUSE;
        MotionEvent.PointerCoords[] coords = {new MotionEvent.PointerCoords()};
        coords[0].x = p[0];
        coords[0].y = p[1];
        coords[0].setAxisValue(AXIS_HSCROLL, hUnits / (float) Wire.SCROLL_UNITS_PER_NOTCH);
        coords[0].setAxisValue(AXIS_VSCROLL, vUnits / (float) Wire.SCROLL_UNITS_PER_NOTCH);
        MotionEvent ev = MotionEvent.obtain(now, now, ACTION_SCROLL, 1, props, coords, 0, 0, 1f, 1f, 0, 0,
                InputDevice.SOURCE_MOUSE, 0);
        send(ev);
    }

    // ------------------------------------------------------------------ touches
    synchronized void key(int action, int keycode, int meta) {
        long now = SystemClock.uptimeMillis();
        KeyEvent ev = new KeyEvent(now, now, action == 1 ? KeyEvent.ACTION_UP : KeyEvent.ACTION_DOWN, keycode, 0,
                meta, KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD);
        send(ev);
    }

    /** Saisie de texte via les touches du clavier virtuel. Les caractères sans touche correspondante sont ignorés. */
    synchronized void text(String text) {
        KeyCharacterMap map = KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD);
        int skipped = 0;
        for (int i = 0; i < text.length(); i++) {
            char[] one = {text.charAt(i)};
            KeyEvent[] events = map.getEvents(one);
            if (events == null) {
                skipped++;
                continue;
            }
            for (KeyEvent e : events) send(e);
        }
        if (skipped > 0) Server.log("Texte : " + skipped + " caractère(s) sans touche correspondante ignoré(s)");
    }

    // ------------------------------------------------------------------ outils
    /** Convertit un point de la vidéo en point de l'écran réel ; null si la taille de la vidéo a changé entre-temps. */
    private float[] toDisplay(int x, int y, int videoW, int videoH) {
        VideoStreamer.Geometry g = video.geometry();
        if (g == null || g.videoW != videoW || g.videoH != videoH) {
            long now = System.currentTimeMillis();
            if (now - lastDropLog > 2000) {
                lastDropLog = now;
                Server.log("Contrôle ignoré : taille de vidéo différente (" + videoW + "x" + videoH + " reçu, "
                        + (g == null ? "inconnue" : g.videoW + "x" + g.videoH) + " actuel)");
            }
            return null;
        }
        float fx = Math.max(0, Math.min(x, g.videoW - 1)) * (float) g.displayW / g.videoW;
        float fy = Math.max(0, Math.min(y, g.videoH - 1)) * (float) g.displayH / g.videoH;
        return new float[]{fx, fy};
    }

    private void send(InputEvent event) {
        try {
            inject.invoke(inputManager, event, INJECT_MODE_ASYNC);
        } catch (Exception e) {
            if (!errorLogged) {            // une seule fois : sinon le journal se remplit à chaque mouvement
                errorLogged = true;
                Server.log("Injection impossible : " + DisplayAccess.describe(e));
            }
        } finally {
            if (event instanceof MotionEvent) ((MotionEvent) event).recycle();
        }
    }
}
