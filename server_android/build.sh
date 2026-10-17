#!/usr/bin/env bash
# Construit server_android/build/andromirror-server.jar (classes.dex dans un jar) avec javac + d8.
# Variables : ANDROID_HOME (ou ANDROID_SDK_ROOT) avec platforms/android-34 et build-tools/34.0.0 ; JAVA_HOME facultatif.
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
PLATFORM="${ANDROID_PLATFORM:-android-34}"
BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-34.0.0}"
ANDROID_JAR="$SDK/platforms/$PLATFORM/android.jar"
D8="$SDK/build-tools/$BUILD_TOOLS/d8"
[ -f "$ANDROID_JAR" ] || { echo "android.jar introuvable : $ANDROID_JAR" >&2; exit 1; }
[ -x "$D8" ] || { echo "d8 introuvable : $D8" >&2; exit 1; }
rm -rf build/classes build/andromirror-server.jar
mkdir -p build/classes
# Java 8 (--release 8) : ce que d8 sait traduire pour Android 8+ (min-api 26)
javac --release 8 -encoding UTF-8 -Xlint:all -cp "$ANDROID_JAR" -d build/classes $(find src -name '*.java')
( cd build/classes && jar cf ../classes.jar . )
"$D8" --release --min-api 26 --lib "$ANDROID_JAR" --output build/andromirror-server.jar build/classes.jar
rm -f build/classes.jar
# Test du format des messages sur un JDK de bureau
javac --release 8 -encoding UTF-8 -d build/test-classes -cp build/classes test/WireSelfTest.java
java -cp build/classes:build/test-classes WireSelfTest > build/wire-selftest.txt
ls -l build/andromirror-server.jar
sha256sum build/andromirror-server.jar | tee build/andromirror-server.jar.sha256
