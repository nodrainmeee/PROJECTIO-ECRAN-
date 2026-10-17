package com.andromirror.server;

import android.content.Context;
import android.content.ContextWrapper;
import android.content.pm.ApplicationInfo;
import android.os.Build;
import android.os.Looper;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;

/**
 * Contexte Android minimal pour un programme lancé par `adb shell` (aucune application installée).
 * Il se présente comme le paquet « com.android.shell » (le vrai propriétaire de l'uid shell), sinon
 * Android refuse certains appels (« le nom du paquet ne correspond pas à l'uid »).
 * Utilisé par la méthode de secours B de DisplayAccess et par la capture du son. Très dépendant de la version
 * d'Android.
 */
final class FakeContext extends ContextWrapper {
    static final String PACKAGE = "com.android.shell";
    private static Context cached;

    private FakeContext(Context base) {
        super(base);
    }

    @Override
    public String getPackageName() {
        return PACKAGE;
    }

    @Override
    public String getOpPackageName() {
        return PACKAGE;
    }

    @Override
    public Context getApplicationContext() {
        return this;
    }

    // Android 12+ : identité d'attribution (construite par réflexion). Cette classe n'est chargée que
    // par la méthode de secours B, réservée à Android 14 et plus.
    @Override
    public android.content.AttributionSource getAttributionSource() {
        try {
            Class<?> b = Class.forName("android.content.AttributionSource$Builder");
            Object builder = b.getConstructor(int.class).newInstance(2000);
            b.getMethod("setPackageName", String.class).invoke(builder, PACKAGE);
            return (android.content.AttributionSource) b.getMethod("build").invoke(builder);
        } catch (Exception e) {
            return null;
        }
    }

    @SuppressWarnings("deprecation")
    static synchronized Context get() throws Exception {
        if (cached != null) return cached;
        try {
            Looper.prepareMainLooper();
        } catch (Exception alreadyPrepared) {
            // ignoré
        }
        Class<?> at = Class.forName("android.app.ActivityThread");
        Constructor<?> ctor = at.getDeclaredConstructor();
        ctor.setAccessible(true);
        Object thread = ctor.newInstance();
        Field current = at.getDeclaredField("sCurrentActivityThread");
        current.setAccessible(true);
        current.set(null, thread);
        Field system = at.getDeclaredField("mSystemThread");
        system.setAccessible(true);
        system.setBoolean(thread, true);
        if (Build.VERSION.SDK_INT <= 30) fillAppInfo(at, thread);   // Android 11 : la capture du son en a besoin
        Context base = (Context) at.getDeclaredMethod("getSystemContext").invoke(thread);
        cached = new FakeContext(base);
        return cached;
    }

    /** Android 11 : déclare une « application liée » (paquet com.android.shell), sinon AudioRecord échoue. */
    private static void fillAppInfo(Class<?> activityThread, Object thread) {
        try {
            Class<?> bindData = Class.forName("android.app.ActivityThread$AppBindData");
            Constructor<?> c = bindData.getDeclaredConstructor();
            c.setAccessible(true);
            Object data = c.newInstance();
            ApplicationInfo info = new ApplicationInfo();
            info.packageName = PACKAGE;
            Field appInfo = bindData.getDeclaredField("appInfo");
            appInfo.setAccessible(true);
            appInfo.set(data, info);
            Field bound = activityThread.getDeclaredField("mBoundApplication");
            bound.setAccessible(true);
            bound.set(thread, data);
        } catch (Exception e) {
            Server.log("FakeContext : informations d'application non définies : " + DisplayAccess.describe(e));
        }
    }

    /** Un DisplayManager qui utilise ce contexte. */
    static Object displayManager() throws Exception {
        Class<?> dm = Class.forName("android.hardware.display.DisplayManager");
        Constructor<?> c = dm.getDeclaredConstructor(Context.class);
        c.setAccessible(true);
        return c.newInstance(get());
    }
}
