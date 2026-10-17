"""Faux adb pour les tests (aucun vrai téléphone n'est utilisé).

Lancé comme `python fake_adb.py <arguments adb>`. Son comportement est piloté par
un fichier JSON dont le chemin est dans la variable ANDROMIRROR_FAKE_STATE :

{
  "mode": "ok" | "hang" | "error" | "garbage",
  "noise": true,                       # ajoute les lignes « * daemon started »
  "log": "chemin/journal.txt",         # une ligne par appel
  "devices": [{"serial": "ABC", "state": "device", "listed_model": "SM_G991B",
               "model": "SM-G991B", "manufacturer": "samsung", "release": "13",
               "sdk": "33", "size": "1080x2400", "override": "720x1600"}]
}
"""
import json
import os
import sys
import time


def out(text: str, stream=None) -> None:
    (stream or sys.stdout).buffer.write(text.encode("utf-8"))
    (stream or sys.stdout).buffer.flush()


def main() -> int:
    args = sys.argv[1:]
    state_path = os.environ.get("ANDROMIRROR_FAKE_STATE")
    state = {"mode": "ok", "devices": []}
    if state_path and os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    if state.get("log"):
        with open(state["log"], "a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n")

    mode = state.get("mode", "ok")
    if mode == "hang":
        time.sleep(60)
        return 0
    if mode == "error":
        out("adb: failed to check server version: cannot connect to daemon\n", sys.stderr)
        return 1

    serial = None
    if len(args) >= 2 and args[0] == "-s":
        serial = args[1]
        args = args[2:]
    devices = state.get("devices", [])

    if not args:
        return 0
    if mode == "garbage" and args[0] == "devices":
        out("\x00\x01 totally not adb output\n???\n")
        return 0
    if args[0] == "version":
        out("Android Debug Bridge version 1.0.41\nVersion 37.0.1-14910828\nInstalled as fake\n")
        return 0
    if args[0] in ("kill-server", "start-server"):
        return 0
    if args[0] == "devices":
        if state.get("noise"):
            out("* daemon not running; starting now at tcp:5037\n* daemon started successfully\n")
        out("List of devices attached\n")
        for d in devices:
            extra = ""
            if d["state"] in ("device",):
                extra = f" usb:1-1 product:p model:{d.get('listed_model', 'Phone')} device:d transport_id:1"
            elif d["state"] == "no permissions":
                out(f"{d['serial']:<22} no permissions (user in plugdev group; are your udev rules wrong?); see [x]\n")
                continue
            out(f"{d['serial']:<22} {d['state']}{extra}\n")
        return 0

    dev = next((d for d in devices if d["serial"] == serial), None)
    if args[0] in ("push", "forward") and (dev is None or dev["state"] != "device"):
        out(f"adb: device '{serial}' not found\n", sys.stderr)
        return 1
    if args[0] == "push":                      # étape 2 : envoi du serveur
        if state.get("push_fail"):
            out("adb: error: failed to copy: No space left on device\n", sys.stderr)
            return 1
        out(f"{args[1]}: 1 file pushed, 0 skipped.\n")
        return 0
    if args[0] == "forward":                   # étape 2 : adb forward tcp:0 localabstract:NAME
        if state.get("forward_fail"):
            out("adb: error: cannot bind listener\n", sys.stderr)
            return 1
        if args[1] != "--remove":
            out(f"{state.get('forward_port', 40123)}\n")
        return 0
    if args[0] == "shell" and len(args) > 1 and args[1].startswith("CLASSPATH="):
        # faux serveur : écrit une ligne puis attend d'être arrêté (le test lance son propre FakeServer en Python)
        if state.get("server_exit"):
            out("[serveur] erreur : aucune méthode de capture d'écran ne fonctionne\n")
            return 1
        out("[serveur] démarrage (faux)\n")
        time.sleep(60)
        return 0
    if args[0] == "shell":
        if dev is None or dev["state"] != "device":
            out(f"adb: device '{serial}' not found\n", sys.stderr)
            return 1
        if args[1:3] == ["getprop", "ro.product.model"]:
            out(dev.get("model", "") + "\n")
        elif args[1:3] == ["getprop", "ro.product.manufacturer"]:
            out(dev.get("manufacturer", "") + "\n")
        elif args[1:3] == ["getprop", "ro.build.version.release"]:
            out(dev.get("release", "") + "\n")
        elif args[1:3] == ["getprop", "ro.build.version.sdk"]:
            out(dev.get("sdk", "") + "\n")
        elif args[1:3] == ["wm", "size"]:
            out(f"Physical size: {dev['size']}\n")
            if dev.get("override"):
                out(f"Override size: {dev['override']}\n")
        else:
            out("\n")
        return 0
    out(f"adb: unknown command {args[0]}\n", sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
