import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAKE = Path(__file__).resolve().parent / "fake_adb.py"

PHONE = {"serial": "R58M123ABC", "state": "device", "listed_model": "SM_G991B", "model": "SM-G991B",
         "manufacturer": "samsung", "release": "13", "sdk": "33", "size": "1080x2400"}


class FakePhone:
    """Pilote le faux adb pendant un test."""

    def __init__(self, tmp: Path):
        self.state_file = tmp / "fake_state.json"
        self.log_file = tmp / "fake_log.txt"
        self.state = {"mode": "ok", "devices": [], "log": str(self.log_file)}
        self.write()

    def write(self):
        self.state_file.write_text(json.dumps(self.state), encoding="utf-8")

    def set_devices(self, *devices, **kw):
        self.state["devices"] = list(devices)
        self.state.update(kw)
        self.write()

    def set_mode(self, mode):
        self.state["mode"] = mode
        self.write()

    @property
    def calls(self):
        return self.log_file.read_text(encoding="utf-8").splitlines() if self.log_file.exists() else []

    @property
    def cmd(self):
        return [sys.executable, str(FAKE)]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Chaque test a son propre dossier utilisateur et ne touche jamais au vrai."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ANDROMIRROR_HOME", str(home))
    monkeypatch.delenv("ANDROMIRROR_ADB", raising=False)
    return home


@pytest.fixture
def phone(tmp_path, monkeypatch):
    p = FakePhone(tmp_path)
    monkeypatch.setenv("ANDROMIRROR_FAKE_STATE", str(p.state_file))
    return p


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["andromirror-tests"])
    yield app


def wait_until(qapp, predicate, timeout=8.0):
    """Fait tourner la boucle Qt jusqu'à ce que predicate() soit vrai."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


@pytest.fixture
def wait(qapp):
    return lambda pred, timeout=8.0: wait_until(qapp, pred, timeout)
