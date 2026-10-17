"""Test de fumée de l'interface en mode hors écran (QT_QPA_PLATFORM=offscreen)."""
import pytest
from PySide6.QtWidgets import QApplication

from andromirror.connection import AdbClient, ConnectionState
from andromirror.connection.monitor import ConnectionController
from andromirror.settings import SettingsStore
from andromirror.ui.main_window import MainWindow
from andromirror.ui.settings_dialog import SettingsDialog
from andromirror.ui.wizard import STEPS, WizardDialog
from conftest import PHONE

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def app_window(phone, qapp, tmp_path):
    store = SettingsStore(tmp_path / "ui_settings.json").load()
    ctl = ConnectionController(AdbClient(phone.cmd), interval_ms=50)
    win = MainWindow(store, ctl, auto_wizard=False)
    win.show()
    ctl.start()
    yield win, store, ctl, phone
    win.close()
    assert ctl.shutdown(8000)


def test_main_window_states(app_window, wait):
    win, store, ctl, phone = app_window
    assert win.windowTitle() == "AndroMirror"
    assert wait(lambda: win.adb_label.text().startswith("ADB 37"))
    assert win.status_title.text() == "Non connecté"
    assert win.btn_connect.text() == "Connecter"

    phone.set_devices(PHONE)
    assert wait(lambda: win._fields["model"].text() != "—")
    assert win._fields["model"].text() == "Samsung SM-G991B"
    assert win._fields["android"].text() == "Android 13 (API 33)"
    assert "1080 × 2400" in win._fields["screen"].text()
    assert not win.warning.isVisible()

    win.btn_connect.click()
    assert wait(lambda: win.status_title.text() == "Connecté")
    assert win.btn_connect.text() == "Déconnecter"

    win.btn_connect.click()
    assert wait(lambda: win.status_title.text() == "Non connecté" and win.btn_connect.text() == "Connecter")

    phone.set_devices({"serial": "U1", "state": "unauthorized"})
    assert wait(lambda: win.status_title.text() == "En attente d'autorisation")
    phone.set_mode("error")
    assert wait(lambda: win.status_title.text() == "Erreur")


def test_old_android_shows_warning(app_window, wait):
    win, store, ctl, phone = app_window
    phone.set_devices({**PHONE, "sdk": "28", "release": "9"})
    assert wait(lambda: win._fields["android"].text().startswith("Android 9"))
    assert win.warning.isVisible() and "Android 11" in win.warning.text()


def test_theme_switch_and_persist(app_window, qapp, tmp_path):
    win, store, ctl, phone = app_window
    store.set("general", "theme", "dark")
    assert win._mode == "dark" and win.btn_theme.text() == "Thème clair"
    assert "#14171f" in qapp.styleSheet()
    win.btn_theme.click()
    assert win._mode == "light" and win.btn_theme.text() == "Thème sombre"
    assert "#f3f5f9" in qapp.styleSheet()
    assert SettingsStore(tmp_path / "ui_settings.json").load().get("general", "theme") == "light"


def test_wizard_three_steps(app_window, wait):
    win, store, ctl, phone = app_window
    assert len(STEPS) == 3
    wiz = WizardDialog()
    assert wiz.step == 0 and not wiz.btn_prev.isEnabled() and wiz.btn_next.text() == "Suivant"
    wiz.btn_next.click()
    assert wiz.step == 1 and wiz.btn_prev.isEnabled()
    wiz.btn_next.click()
    assert wiz.step == 2 and wiz.btn_next.text() == "Terminer"
    wiz.btn_prev.click()
    assert wiz.step == 1
    wiz.btn_next.click()
    done = []
    wiz.finished_ok.connect(lambda: done.append(1))
    wiz.btn_next.click()
    assert done == [1] and wiz.result() == wiz.DialogCode.Accepted
    titles = [t for t, _ in STEPS]
    assert "options pour les développeurs" in titles[0] and "débogage usb" in titles[1].lower()
    assert "Brancher" in titles[2]


def test_wizard_live_status(app_window, wait):
    win, store, ctl, phone = app_window
    wiz = WizardDialog()
    ctl.snapshot_changed.connect(wiz.on_snapshot)
    phone.set_devices({"serial": "U1", "state": "unauthorized"})
    assert wait(lambda: "Autoriser" in wiz.live_text())
    phone.set_devices(PHONE)
    assert wait(lambda: "Samsung SM-G991B" in wiz.live_text())


def test_wizard_marks_done_via_window(app_window):
    win, store, ctl, phone = app_window
    assert store.get("general", "wizard_done") is False
    wiz = WizardDialog(win)
    wiz.finished_ok.connect(lambda: store.set("general", "wizard_done", True))
    for _ in range(3):
        wiz.btn_next.click()
    assert store.get("general", "wizard_done") is True


def test_first_run_opens_wizard(phone, qapp, tmp_path, monkeypatch, wait):
    opened = []
    monkeypatch.setattr(MainWindow, "open_wizard", lambda self: opened.append(1))
    store = SettingsStore(tmp_path / "s.json").load()
    ctl = ConnectionController(AdbClient(phone.cmd), interval_ms=50)
    win = MainWindow(store, ctl, auto_wizard=True)
    win.show()
    assert wait(lambda: opened)
    win.close()
    ctl.shutdown()
    # déjà fait : ne s'ouvre plus
    opened.clear()
    store.set("general", "wizard_done", True)
    ctl2 = ConnectionController(AdbClient(phone.cmd), interval_ms=50)
    win2 = MainWindow(store, ctl2, auto_wizard=True)
    win2.show()
    wait(lambda: False, timeout=0.3)
    assert not opened
    win2.close()
    ctl2.shutdown()


def test_settings_dialog_saves(tmp_path):
    store = SettingsStore(tmp_path / "s.json").load()
    dlg = SettingsDialog(store)
    dlg.bitrate.setValue(30)
    dlg.fps.setCurrentIndex(dlg.fps.findData(30))
    dlg.codec.setCurrentIndex(dlg.codec.findData("h265"))
    dlg.resolution.setCurrentIndex(dlg.resolution.findData("1080"))
    dlg.forward_audio.setChecked(False)
    dlg.volume.setValue(55)
    dlg.theme.setCurrentIndex(dlg.theme.findData("dark"))
    dlg.auto_reconnect.setChecked(False)
    dlg.keep_screen_off.setChecked(True)
    reloaded = SettingsStore(tmp_path / "s.json").load()
    assert reloaded.get("quality", "bitrate_mbps") == 30
    assert reloaded.get("quality", "fps") == 30
    assert reloaded.get("quality", "codec") == "h265"
    assert reloaded.get("quality", "resolution") == "1080"
    assert reloaded.get("audio", "forward_audio") is False
    assert reloaded.get("audio", "volume") == 55
    assert reloaded.get("general", "theme") == "dark"
    assert reloaded.get("general", "auto_reconnect") is False
    assert reloaded.get("general", "keep_screen_off") is True
    assert "Enregistré" in dlg.saved_label.text()

    dlg.btn_reset.click()
    after = SettingsStore(tmp_path / "s.json").load()
    assert after.get("quality", "bitrate_mbps") == 16 and after.get("general", "theme") == "system"
    assert dlg.bitrate.value() == 16 and dlg.auto_reconnect.isChecked()


def test_settings_changes_reach_controller(app_window, wait, monkeypatch):
    win, store, ctl, phone = app_window
    seen = []
    monkeypatch.setattr(ctl, "set_auto_reconnect", lambda v: seen.append(v))
    store.set("general", "auto_reconnect", False)
    assert seen == [False]


def test_close_saves_window_size(app_window, tmp_path):
    win, store, ctl, phone = app_window
    win.resize(900, 700)
    win.close()
    r = SettingsStore(tmp_path / "ui_settings.json").load()
    assert (r.get("window", "width"), r.get("window", "height")) == (900, 700)


def test_grab_renders(app_window, wait):
    win, *_ = app_window
    img = win.grab()
    assert not img.isNull() and img.width() >= 560


def test_show_fatal_runs_in_gui_thread(app_window, monkeypatch):
    win, *_ = app_window
    from andromirror.ui import main_window
    shown = []
    monkeypatch.setattr(main_window.QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
    win.fatal_requested.emit("Boum")
    assert shown and "Boum" in shown[0]
