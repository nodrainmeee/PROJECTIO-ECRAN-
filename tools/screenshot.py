"""Génère des captures de l'interface avec un FAUX téléphone (aucun vrai appareil).

Usage : QT_QPA_PLATFORM=offscreen python tools/screenshot.py sortie.png [clair|sombre] [assistant|reglages]
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = Path(tempfile.mkdtemp())
os.environ["ANDROMIRROR_HOME"] = str(tmp / "home")
state = tmp / "state.json"
state.write_text(json.dumps({"mode": "ok", "devices": [
    {"serial": "R58M123ABC", "state": "device", "listed_model": "SM_G991B", "model": "SM-G991B",
     "manufacturer": "samsung", "release": "13", "sdk": "33", "size": "1080x2400"}]}))
os.environ["ANDROMIRROR_FAKE_STATE"] = str(state)

from PySide6.QtWidgets import QApplication  # noqa: E402

from andromirror.connection import AdbClient  # noqa: E402
from andromirror.connection.monitor import ConnectionController  # noqa: E402
from andromirror.settings import SettingsStore  # noqa: E402
from andromirror.ui.main_window import MainWindow  # noqa: E402
from andromirror.ui.settings_dialog import SettingsDialog  # noqa: E402
from andromirror.ui.wizard import WizardDialog  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "andromirror-interface.png"
mode = sys.argv[2] if len(sys.argv) > 2 else "sombre"
what = sys.argv[3] if len(sys.argv) > 3 else "principale"

app = QApplication(sys.argv[:1])
store = SettingsStore(tmp / "s.json").load()
store.set("general", "theme", "dark" if mode == "sombre" else "light")
fake = [sys.executable, str(ROOT / "tests" / "fake_adb.py")]
ctl = ConnectionController(AdbClient(fake), interval_ms=100)
win = MainWindow(store, ctl, auto_wizard=False)
win.resize(760, 640)
win.show()
ctl.start()


def pump(cond, t=8):
    end = time.time() + t
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.01)


pump(lambda: win._fields["model"].text() != "—")
ctl.connect_device()
pump(lambda: win.status_title.text() == "Connecté")
app.processEvents()
if what == "assistant":
    w = WizardDialog(win)
    w.btn_next.click()
    w.btn_next.click()
    ctl.snapshot_changed.connect(w.on_snapshot)
    w.on_snapshot(ctl.last_snapshot)
    w.show()
    app.processEvents()
    w.grab().save(out)
elif what == "reglages":
    d = SettingsDialog(store, win)
    d.show()
    app.processEvents()
    d.grab().save(out)
else:
    win.grab().save(out)
win.close()
ctl.shutdown()
print("OK", out)
