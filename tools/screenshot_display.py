"""Capture de la fenêtre d'affichage avec un FAUX téléphone (mire de test synthétique, aucun vrai appareil).

Usage : QT_QPA_PLATFORM=offscreen python tools/screenshot_display.py sortie.png [portrait|paysage|plein]
Utilise le faux serveur et le générateur de mire des tests (dossier tests/) : il faut PyAV et Pillow.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ANDROMIRROR_NO_GL", "1")      # la capture hors écran utilise le rendu logiciel
tmp = Path(tempfile.mkdtemp())
os.environ["ANDROMIRROR_HOME"] = str(tmp / "home")

from PySide6.QtWidgets import QApplication  # noqa: E402

from andromirror.connection.models import DeviceInfo  # noqa: E402
from andromirror.connection.server_launcher import RunningServer  # noqa: E402
from andromirror.settings import SettingsStore  # noqa: E402
from andromirror.ui import theme  # noqa: E402
from andromirror.ui.display_manager import DisplayManager  # noqa: E402
from andromirror.video.session import ScreenSession  # noqa: E402
from fake_server import FakeServer  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "andromirror-affichage.png"
mode = sys.argv[2] if len(sys.argv) > 2 else "portrait"


class _Proc:
    def poll(self):
        return None


class _Launcher:
    def __init__(self, server):
        self.server = server

    def start(self):
        return RunningServer(self.server.port, bytes(range(16)), _Proc())

    def failure_hint(self):
        return ""

    def cleanup(self):
        pass


app = QApplication(sys.argv[:1])
store = SettingsStore(tmp / "s.json").load()
theme.apply_theme(app, "dark")
sizes = [(540, 1170)] if mode == "portrait" else [(1170, 540)]
server = FakeServer(sizes=sizes, frames_per_size=300, fps=30, name="Samsung SM-G991B (test)").start()
dm = DisplayManager(store, None, session_factory=lambda serial, st: ScreenSession(
    None, serial, st, launcher_factory=lambda: _Launcher(server)))
dm.start(DeviceInfo(serial="TEST", model="SM-G991B", manufacturer="samsung", android_release="13", sdk=33,
                    screen=(1080, 2340)))
win = dm.window
end = time.time() + 15
while time.time() < end and not (win.video.has_frame and "i/s" in win.info_label.text()
                                 and win.info_label.text().count("·") >= 3):
    app.processEvents()
    time.sleep(0.02)
for _ in range(15):          # quelques images de plus : le carré blanc de la mire a bougé
    app.processEvents()
    time.sleep(0.03)
if mode == "plein":
    win.resize(1280, 720)
win.grab().save(out)
dm.shutdown()
server.close()
print("OK", out, win.size().width(), "x", win.size().height())
