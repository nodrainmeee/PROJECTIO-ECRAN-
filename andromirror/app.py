"""Démarrage de l'application."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .connection.monitor import ConnectionController
from .logging_setup import get_logger, install_excepthooks, setup_logging
from .settings import SettingsStore
from .ui import theme
from .ui.main_window import MainWindow


def selftest(result_path: str) -> int:
    """Auto-test du programme figé (utilisé par la CI) : adb embarqué, fenêtre, FFmpeg (PyAV), serveur Android embarqué,
    fenêtre d'affichage.

    Écrit « OK ... » ou « ECHEC ... » dans result_path (un .exe fenêtré n'a pas de console).
    """
    from pathlib import Path

    from .connection.adb import AdbClient
    problems = []
    info = []
    try:
        app = QApplication.instance() or QApplication(sys.argv[:1])
        client = AdbClient()
        version = client.version() if client.available else ""
        if not version:
            problems.append("adb embarqué introuvable ou muet")
        info.append(f"adb={version or '?'}")
        store = SettingsStore().load()
        theme.apply_theme(app, store.get("general", "theme"))
        controller = ConnectionController(client)
        window = MainWindow(store, controller, auto_wizard=False)
        window.show()
        app.processEvents()
        if window.grab().isNull():
            problems.append("la fenêtre ne s'affiche pas")
        info.append("fenetre=ok")
        window.close()
        info.append(_selftest_video(problems))
        info.append(_selftest_server(problems))
        info.append(_selftest_display(problems, app))
        info.append(_selftest_audio())
    except Exception as exc:  # noqa: BLE001
        problems.append(f"exception : {exc!r}")
    text = ("ECHEC " + "; ".join(problems)) if problems else "OK " + " ".join(info)
    Path(result_path).write_text(text + "\n", encoding="utf-8")
    return 1 if problems else 0


def _selftest_video(problems) -> str:
    """PyAV/FFmpeg embarqué : encode une image de test en H.264 puis la décode avec notre décodeur."""
    try:
        import av
        from fractions import Fraction

        from .video.decoder import VideoDecoder
        enc = av.CodecContext.create("libx264", "w")
        enc.width, enc.height, enc.pix_fmt = 96, 64, "yuv420p"
        enc.time_base, enc.framerate = Fraction(1, 30), Fraction(30, 1)
        enc.options = {"preset": "ultrafast", "tune": "zerolatency", "x264-params": "repeat-headers=1"}
        enc.open()
        frame = av.VideoFrame(96, 64, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes([128]) * plane.buffer_size)
        frame.pts = 0
        packets = list(enc.encode(frame)) + list(enc.encode(None))
        dec = VideoDecoder("h264", prefer_hw=False)
        out = []
        for p in packets:
            out += dec.decode(bytes(p))
        if not out or (out[0].native_width, out[0].native_height) != (96, 64):
            problems.append("PyAV : l'image de test n'a pas été décodée à la bonne taille")
            return "decodage=ECHEC"
        return f"pyav={av.__version__}(decodage ok)"
    except Exception as exc:  # noqa: BLE001
        problems.append(f"PyAV/FFmpeg : {exc!r}")
        return "decodage=ECHEC"


def _selftest_server(problems) -> str:
    """Le serveur Android (andromirror-server.jar) doit être embarqué dans le programme figé."""
    import hashlib
    import zipfile

    from .connection.server_launcher import find_server_jar
    jar = find_server_jar()
    if jar is None:
        if getattr(sys, "frozen", False):
            problems.append("andromirror-server.jar absent du programme")
            return "serveur=ABSENT"
        return "serveur=absent(non fige)"
    try:
        with zipfile.ZipFile(jar) as z:
            if "classes.dex" not in z.namelist():
                problems.append("andromirror-server.jar ne contient pas classes.dex")
                return "serveur=INVALIDE"
        digest = hashlib.sha256(jar.read_bytes()).hexdigest()[:12]
        return f"serveur=ok({jar.stat().st_size}o,{digest})"
    except Exception as exc:  # noqa: BLE001
        problems.append(f"andromirror-server.jar illisible : {exc!r}")
        return "serveur=ILLISIBLE"


def _selftest_audio() -> str:
    """La bibliothèque de sortie audio (PortAudio) se charge. Non bloquant : sans elle, le logiciel fonctionne, sans le son."""
    try:
        import sounddevice as sd
        sd.query_hostapis()
        return "son=ok"
    except ImportError:
        return "son=ABSENT"
    except Exception as exc:  # noqa: BLE001
        return f"son=indisponible({type(exc).__name__})"


def _selftest_display(problems, app) -> str:
    """La fenêtre d'affichage se crée et dessine une image de test (rendu logiciel, sans carte graphique)."""
    try:
        from .ui.display_window import DisplayWindow
        from .video.decoder import DecodedFrame
        win = DisplayWindow("auto-test", force_software=True)
        win.resize(320, 480)
        win.show()
        win.video.set_frame(DecodedFrame(32, 64, 96, bytes([200, 40, 40]) * (32 * 64), 0, 32, 64))
        app.processEvents()
        img = win.grab().toImage()
        win.close_programmatically()
        if img.isNull():
            problems.append("la fenêtre d'affichage ne dessine pas")
            return "affichage=ECHEC"
        return "affichage=ok"
    except Exception as exc:  # noqa: BLE001
        problems.append(f"fenêtre d'affichage : {exc!r}")
        return "affichage=ECHEC"


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv)
    if len(args) >= 3 and args[1] == "--selftest":
        setup_logging()
        return selftest(args[2])
    setup_logging()
    log = get_logger("app")
    log.info("Démarrage de %s %s", APP_NAME, __version__)

    app = QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    store = SettingsStore().load()
    theme.apply_theme(app, store.get("general", "theme"))

    controller = ConnectionController(auto_reconnect=store.get("general", "auto_reconnect"))
    window = MainWindow(store, controller)
    install_excepthooks(on_error=window.fatal_requested.emit)
    controller.start()
    window.show()
    code = app.exec()
    controller.shutdown()
    log.info("Arrêt de %s (code %s)", APP_NAME, code)
    return code
