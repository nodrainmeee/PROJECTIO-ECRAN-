package com.andromirror.server;

import android.graphics.Rect;
import android.os.IBinder;
import android.view.Surface;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

/**
 * Accès à l'écran du téléphone par des API Android CACHÉES (« hidden APIs »), appelées par réflexion.
 *
 * Pourquoi : un programme lancé par `adb shell` (identité « shell », uid 2000) peut demander à Android
 * une copie de l'écran sans passer par la fenêtre d'autorisation de MediaProjection. Ces API n'ont aucune
 * garantie de stabilité : leur forme change d'une version d'Android (et d'une marque) à l'autre.
 * Chaque appel est donc protégé : en cas d'échec, on essaie la méthode suivante, sinon on explique
 * l'échec clairement (voir docs dans server_android/README.md).
 *
 * Deux méthodes, essayées dans cet ordre :
 *   A. SurfaceControl.createDisplay + setDisplaySurface/Projection/LayerStack  (Android 8 → 14, en général)
 *   B. DisplayManager.createVirtualDisplay(nom, l, h, idÉcranÀCopier, surface)  (Android 14+, secours)
 *
 * Règle absolue : l'affichage créé n'est JAMAIS « sécurisé » (secure=false). Les contenus protégés
 * (applications bancaires, vidéo sous DRM...) apparaissent donc noirs : on ne contourne pas ces protections.
 */
final class DisplayAccess {
    private DisplayAccess() {}

    /** État de l'écran principal à un instant donné. */
    static final class State {
        final int width, height, rotation, layerStack;

        State(int width, int height, int rotation, int layerStack) {
            this.width = width;
            this.height = height;
            this.rotation = rotation;
            this.layerStack = layerStack;
        }

        boolean sameAs(State o) {
            return o != null && width == o.width && height == o.height && rotation == o.rotation
                    && layerStack == o.layerStack;
        }

        @Override
        public String toString() {
            return width + "x" + height + " rot=" + rotation + " layerStack=" + layerStack;
        }
    }

    /** Une copie d'écran active. */
    interface Capture {
        void release();

        String method();
    }

    // ------------------------------------------------------------------ état de l'écran
    static State query() throws Exception {
        Class<?> dmg = Class.forName("android.hardware.display.DisplayManagerGlobal");
        Object global = dmg.getMethod("getInstance").invoke(null);
        Object info = dmg.getMethod("getDisplayInfo", int.class).invoke(global, 0);
        if (info == null) {
            throw new IllegalStateException("DisplayManagerGlobal.getDisplayInfo a renvoyé null");
        }
        Class<?> c = info.getClass();
        int w = intField(c, info, "logicalWidth");
        int h = intField(c, info, "logicalHeight");
        int rot = intField(c, info, "rotation");
        int stack = 0;
        try {
            stack = intField(c, info, "layerStack");
        } catch (Exception ignored) {
            // absent sur certaines versions : 0 = écran principal
        }
        if (w <= 0 || h <= 0) {
            throw new IllegalStateException("taille d'écran invalide : " + w + "x" + h);
        }
        return new State(w, h, rot, stack);
    }

    private static int intField(Class<?> c, Object o, String name) throws Exception {
        Field f = c.getDeclaredField(name);
        f.setAccessible(true);
        return f.getInt(o);
    }

    // ------------------------------------------------------------------ capture
    /** Démarre la copie de l'écran vers `surface` (taille de la vidéo : vw x vh). */
    static Capture start(Surface surface, State s, int vw, int vh) throws Exception {
        Exception first = null;
        try {
            return startWithSurfaceControl(surface, s, vw, vh);
        } catch (Exception e) {
            first = e;
            Server.log("Méthode A (SurfaceControl) impossible : " + describe(e));
        }
        try {
            return startWithDisplayManager(surface, vw, vh);
        } catch (Exception e) {
            Server.log("Méthode B (DisplayManager) impossible : " + describe(e));
            throw new IllegalStateException("aucune méthode de capture d'écran ne fonctionne sur ce téléphone ("
                    + describe(first) + " / " + describe(e) + ")");
        }
    }

    static String describe(Throwable t) {
        Throwable r = t;
        while (r.getCause() != null && r != r.getCause()) r = r.getCause();
        return r.getClass().getSimpleName() + ": " + r.getMessage();
    }

    // ---- méthode A
    private static Capture startWithSurfaceControl(Surface surface, State s, int vw, int vh) throws Exception {
        final Class<?> sc = Class.forName("android.view.SurfaceControl");
        final Method destroy = sc.getMethod("destroyDisplay", IBinder.class);
        final IBinder token = (IBinder) sc.getMethod("createDisplay", String.class, boolean.class)
                .invoke(null, "AndroMirror", false);   // false : pas d'affichage sécurisé (pas de contournement)
        if (token == null) throw new IllegalStateException("createDisplay a renvoyé null");
        try {
            Method open = sc.getMethod("openTransaction");
            Method close = sc.getMethod("closeTransaction");
            Method setSurface = sc.getMethod("setDisplaySurface", IBinder.class, Surface.class);
            Method setProjection = sc.getMethod("setDisplayProjection", IBinder.class, int.class, Rect.class,
                    Rect.class);
            Method setLayerStack = sc.getMethod("setDisplayLayerStack", IBinder.class, int.class);
            open.invoke(null);
            try {
                setSurface.invoke(null, token, surface);
                // orientation 0 : l'image garde l'orientation actuelle de l'écran ; la rotation du téléphone
                // est annoncée au PC par un changement de taille.
                setProjection.invoke(null, token, 0, new Rect(0, 0, s.width, s.height), new Rect(0, 0, vw, vh));
                setLayerStack.invoke(null, token, s.layerStack);
            } finally {
                close.invoke(null);
            }
        } catch (Exception e) {
            try {
                destroy.invoke(null, token);
            } catch (Exception ignored) {
                // rien de plus à faire
            }
            throw e;
        }
        return new Capture() {
            @Override
            public void release() {
                try {
                    destroy.invoke(null, token);
                } catch (Exception e) {
                    Server.log("destroyDisplay : " + describe(e));
                }
            }

            @Override
            public String method() {
                return "SurfaceControl";
            }
        };
    }

    // ---- méthode B
    private static Capture startWithDisplayManager(Surface surface, int vw, int vh) throws Exception {
        final Object dm = FakeContext.displayManager();
        Method create = null;
        for (Method m : dm.getClass().getMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (m.getName().equals("createVirtualDisplay") && p.length == 5 && p[0] == String.class
                    && p[1] == int.class && p[2] == int.class && p[3] == int.class && p[4] == Surface.class) {
                create = m;
                break;
            }
        }
        if (create == null) {
            throw new NoSuchMethodException("DisplayManager.createVirtualDisplay(String,int,int,int,Surface) absent");
        }
        final Object vd = create.invoke(dm, "AndroMirror", vw, vh, 0, surface);
        if (vd == null) throw new IllegalStateException("createVirtualDisplay a renvoyé null");
        return new Capture() {
            @Override
            public void release() {
                try {
                    vd.getClass().getMethod("release").invoke(vd);
                } catch (Exception e) {
                    Server.log("VirtualDisplay.release : " + describe(e));
                }
            }

            @Override
            public String method() {
                return "DisplayManager";
            }
        };
    }
}
