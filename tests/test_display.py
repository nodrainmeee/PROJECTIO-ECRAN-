"""Session d'affichage de bout en bout : faux serveur (socket) -> récepteur -> décodeur -> fenêtre (hors écran)."""
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from andromirror.connection import AdbClient
from andromirror.connection.monitor import ConnectionController
from andromirror.connection.server_launcher import LaunchError, RunningServer
from andromirror.settings import SettingsStore, defaults
from andromirror.ui.display_manager import DisplayManager
from andromirror.ui.display_window import DisplayWindow
from andromirror.ui.main_window import MainWindow
from andromirror.video.session import ScreenSession
from conftest import PHONE
from fake_server import FakeServer

pytestmark = pytest.mark.usefixtures("qapp")


class FakeProc:
    def poll(self):
        return None


class FakeLauncher:
    """Remplace ServerLauncher : le « téléphone » est un FakeServer local."""

    def __init__(self, server=None, fail=None, hint=""):
        self.server, self.fail, self.hint = server, fail, hint
        self.cleaned = 0
        self.started = 0
        self.token = bytes(range(16))

    def start(self):
        self.started += 1
        if self.fail:
            raise LaunchError(self.fail)
        return RunningServer(self.server.port, self.token, FakeProc())

    def failure_hint(self):
        return self.hint

    def cleanup(self):
        self.cleaned += 1


def pump(qapp, pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return pred()


class Spy:
    def __init__(self, session):
        self.hello, self.sizes, self.ended, self.frames, self.decoders, self.messages, self.progress = \
            [], [], [], 0, [], [], []
        session.hello.connect(self.hello.append)
        session.video_size.connect(lambda w, h, r: self.sizes.append((w, h, r)))
        session.ended.connect(self.ended.append)
        session.frame_ready.connect(self._frame)
        session.decoder_info.connect(self.decoders.append)
        session.server_message.connect(lambda t, f: self.messages.append((t, f)))
        session.progress.connect(self.progress.append)

    def _frame(self):
        self.frames += 1


def make_session(server, **kw):
    la = FakeLauncher(server, **kw)
    s = ScreenSession(None, "SER", defaults(), launcher_factory=lambda: la)
    return s, la, Spy(s)


# --------------------------------------------------------------------------- session
def test_session_streams_frames_and_orientation_change(qapp):
    srv = FakeServer(sizes=[(160, 320), (320, 160)], frames_per_size=12, expected_token=bytes(range(16))).start()
    s, la, spy = make_session(srv)
    s.set_target_size(400, 400)
    s.start()
    assert pump(qapp, lambda: spy.hello)
    assert (spy.hello[0].width, spy.hello[0].height) == (160, 320)
    assert pump(qapp, lambda: spy.sizes)
    assert spy.sizes == [(320, 160, 1)]
    assert pump(qapp, lambda: s.stats().frames_decoded >= 20)
    frame = s.take_frame()
    assert frame is not None and (frame.native_width, frame.native_height) in {(160, 320), (320, 160)}
    assert spy.decoders and spy.decoders[0] in ("logiciel",) or "matériel" in spy.decoders[0]
    assert s.stats().fps > 0 and s.bytes_received > 0
    s.stop(wait=True)
    assert pump(qapp, lambda: spy.ended)
    assert spy.ended[0].reason == "stopped" and la.cleaned == 1
    assert srv.quit_received
    srv.close()


def test_session_server_disconnect_is_reported_and_phone_cleaned(qapp):
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=40, drop_after=8).start()
    s, la, spy = make_session(srv, hint="")
    s.start()
    assert pump(qapp, lambda: spy.ended)
    assert spy.ended[0].reason == "connection_lost" and la.cleaned == 1
    assert not s.running
    srv.close()


def test_session_launch_failure_reports_message_and_cleans(qapp):
    s, la, spy = make_session(None, fail="Impossible d'envoyer le serveur sur le téléphone.")
    s.start()
    assert pump(qapp, lambda: spy.ended)
    assert spy.ended[0].reason == "launch_failed" and "envoyer le serveur" in spy.ended[0].message
    assert la.cleaned == 1


class DeadServerLauncher(FakeLauncher):
    def start(self):
        class Dead:
            def poll(self):
                return 1
        return RunningServer(1, self.token, Dead())     # port 1 : rien n'écoute


def test_session_connect_failed_shows_server_last_line(qapp):
    la = DeadServerLauncher(hint="[serveur] erreur : aucune méthode de capture d'écran ne fonctionne")
    s = ScreenSession(None, "SER", defaults(), launcher_factory=lambda: la)
    spy = Spy(s)
    s.start()
    assert pump(qapp, lambda: spy.ended)
    info = spy.ended[0]
    assert info.reason == "connect_failed" and "aucune méthode de capture" in info.message and la.cleaned == 1


def test_session_fatal_message_from_server(qapp):
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=3, fatal_message="Le serveur du téléphone a rencontré une erreur : X",
                     keep_open=False).start()
    s, la, spy = make_session(srv)
    s.start()
    assert pump(qapp, lambda: spy.ended)
    assert spy.ended[0].reason == "fatal_message" and "erreur : X" in spy.ended[0].message
    # Le texte fatal arrive par `ended` (c'est ce que l'interface affiche). Le signal `server_message` est un doublon émis par le
    # thread de décodage : si la session se termine avant que ce thread ait traité le paquet (décodeur lent à démarrer, ex. repli
    # matériel -> logiciel sous Windows), il peut ne pas partir du tout ; s'il part, il doit être marqué fatal.
    assert all(fatal for _text, fatal in spy.messages)
    srv.close()


def test_session_decoder_failure_ends_cleanly(qapp, monkeypatch):
    from andromirror.video import pipeline

    def broken(*a, **k):
        raise RuntimeError("FFmpeg absent")
    monkeypatch.setattr(pipeline, "VideoDecoder", broken)
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=5).start()
    s, la, spy = make_session(srv)
    s.start()
    assert pump(qapp, lambda: spy.ended)
    assert spy.ended[0].reason == "decoder_failed" and "FFmpeg absent" in spy.ended[0].message
    assert la.cleaned == 1
    srv.close()


def test_stop_before_server_answers_does_not_hang(qapp):
    srv = FakeServer(refuse_first=10_000).start()
    s, la, spy = make_session(srv)
    s.start()
    time.sleep(0.3)
    t0 = time.time()
    s.stop(wait=True)
    assert time.time() - t0 < 4
    assert pump(qapp, lambda: spy.ended)
    assert spy.ended[0].reason == "stopped" and la.cleaned == 1
    srv.close()


def test_ended_is_emitted_only_once(qapp):
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=5).start()
    s, la, spy = make_session(srv)
    s.start()
    assert pump(qapp, lambda: spy.hello)
    s.stop(wait=True)
    s.stop(wait=True)
    pump(qapp, lambda: False, 0.3)
    assert len(spy.ended) == 1
    srv.close()


# --------------------------------------------------------------------------- fenêtre + gestionnaire
@pytest.fixture
def manager(qapp, tmp_path):
    store = SettingsStore(tmp_path / "s.json").load()
    servers = []

    def factory(serial, settings):
        srv = servers[-1]
        return ScreenSession(None, serial, settings, launcher_factory=lambda: FakeLauncher(srv))
    m = DisplayManager(store, None, session_factory=factory)
    m.servers = servers
    m.notes = []
    m.finished_infos = []
    m.note.connect(m.notes.append)
    m.finished.connect(m.finished_infos.append)
    yield m
    m.shutdown()
    for s in servers:
        s.close()


def device():
    from andromirror.connection.models import DeviceInfo
    return DeviceInfo(serial="SER", model="SM-G991B", manufacturer="samsung", android_release="13", sdk=33,
                      screen=(1080, 2400))


def test_window_shows_frames_adapts_orientation_and_reports_stats(manager, qapp):
    manager.servers.append(FakeServer(sizes=[(180, 360), (360, 180)], frames_per_size=40, fps=30).start())
    manager.start(device())
    win = manager.window
    assert win is not None and "Samsung SM-G991B" in win.windowTitle()
    assert pump(qapp, lambda: win.video.has_frame)
    assert win.orientation == "portrait" and win.height() > win.width()
    assert pump(qapp, lambda: win.orientation == "landscape")
    assert win.width() > win.height()                         # fenêtre adaptée au mode paysage (F7)
    assert pump(qapp, lambda: "H.264" in win.info_label.text() and "i/s" in win.info_label.text())
    assert "décodage logiciel" in win.info_label.text() or "décodage matériel" in win.info_label.text()
    assert any("Affichage de Fake Phone" in n for n in manager.notes)
    # F11 -> plein écran, Échap -> retour ; Échap hors plein écran ne ferme rien
    QTest.keyClick(win, Qt.Key.Key_F11)
    assert win.isFullScreen() and not win.toolbar.isVisible()
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert not win.isFullScreen() and win.toolbar.isVisibleTo(win)
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert win.isVisible() and manager.active


def test_user_closing_window_stops_session_cleanly(manager, qapp):
    srv = FakeServer(sizes=[(180, 360)], frames_per_size=30).start()
    manager.servers.append(srv)
    closed = []
    manager.user_closed.connect(lambda: closed.append(1))
    manager.start(device())
    win = manager.window
    assert pump(qapp, lambda: win.video.has_frame)
    win.close()
    assert closed == [1]
    assert pump(qapp, lambda: manager.finished_infos)
    assert manager.finished_infos[0].reason == "stopped"
    assert not manager.active and manager.window is None
    assert srv.quit_received                                   # le serveur du téléphone est prévenu


def test_stop_with_message_for_unplugged_phone(manager, qapp):
    manager.servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=30).start())
    manager.start(device())
    assert pump(qapp, lambda: manager.window.video.has_frame)
    manager.stop("Téléphone débranché : l'affichage a été fermé.")
    assert pump(qapp, lambda: manager.finished_infos)
    assert manager.notes[-1] == "Téléphone débranché : l'affichage a été fermé."
    assert not manager.active


def test_cable_cut_during_stream_closes_window_with_french_message(manager, qapp):
    manager.servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=60, drop_after=10).start())
    manager.start(device())
    win = manager.window
    assert pump(qapp, lambda: manager.finished_infos)
    assert manager.finished_infos[0].reason == "connection_lost"
    assert "connexion avec le téléphone a été coupée" in manager.notes[-1]
    assert manager.window is None and not manager.active


def test_launch_error_closes_window_and_explains(manager, qapp):
    def factory(serial, settings):
        return ScreenSession(None, serial, settings, launcher_factory=lambda: FakeLauncher(fail="Impossible d'envoyer le serveur."))
    manager._session_factory = factory
    manager.start(device())
    assert pump(qapp, lambda: manager.finished_infos)
    assert manager.notes[-1] == "Impossible d'envoyer le serveur."
    assert manager.window is None


def test_start_twice_is_ignored(manager, qapp):
    manager.servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=10).start())
    manager.start(device())
    first = manager.session
    manager.start(device())
    assert manager.session is first


def test_toolbar_placeholders_are_disabled(qapp):
    win = DisplayWindow("Test", force_software=True)
    assert win.placeholders and all(not a.isEnabled() for a in win.placeholders)
    assert win.act_fullscreen.isEnabled()
    win.close_programmatically()


def test_fit_to_video_keeps_aspect_and_fits_screen(qapp):
    win = DisplayWindow("Test", force_software=True)
    win.show()
    qapp.processEvents()
    avail = win.screen().availableGeometry()
    for (w, h) in [(1080, 2400), (2400, 1080), (3840, 2160)]:
        win.fit_to_video(w, h, force=True)
        qapp.processEvents()
        assert win.frameGeometry().width() <= avail.width() and win.frameGeometry().height() <= avail.height()
        # La fenêtre garde le ratio, sauf si la largeur minimale (barre d'outils) l'en empêche sur un petit écran :
        # dans ce cas l'image est simplement centrée avec des bandes (fit_rect), jamais déformée.
        at_min_width = win.video.width() <= win.minimumWidth()
        assert at_min_width or abs(win.video.width() / win.video.height() - w / h) < 0.05
    win.close_programmatically()


# --------------------------------------------------------------------------- fenêtre principale
@pytest.fixture
def app_with_display(phone, qapp, tmp_path):
    store = SettingsStore(tmp_path / "ui.json").load()
    ctl = ConnectionController(AdbClient(phone.cmd), interval_ms=50)
    servers = []
    launchers = []

    def factory(serial, settings):
        launcher = FakeLauncher(servers[-1])
        launchers.append(launcher)
        return ScreenSession(None, serial, settings, launcher_factory=lambda: launcher)
    dm = DisplayManager(store, ctl.adb, session_factory=factory)
    win = MainWindow(store, ctl, auto_wizard=False, display=dm)
    win.show()
    ctl.start()
    yield win, phone, dm, servers, launchers
    win.close()
    assert ctl.shutdown(8000)
    for s in servers:
        s.close()


def test_connect_opens_display_disconnect_closes_it(app_with_display, wait):
    win, phone, dm, servers, launchers = app_with_display
    servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=200, fps=30).start())
    phone.set_devices(PHONE)
    assert wait(lambda: win._fields["model"].text() != "—")
    assert not dm.active
    win.btn_connect.click()
    assert wait(lambda: dm.window is not None and dm.window.video.has_frame)
    assert dm.window.isVisible() and "Affichage de Fake Phone" in win.preview_msg.text()
    win.btn_connect.click()                                     # Déconnecter
    assert wait(lambda: not dm.active and win.btn_connect.text() == "Connecter")
    assert launchers[0].cleaned == 1
    assert servers[0].quit_received


def test_closing_display_window_disconnects(app_with_display, wait):
    win, phone, dm, servers, launchers = app_with_display
    servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=200, fps=30).start())
    phone.set_devices(PHONE)
    assert wait(lambda: win._fields["model"].text() != "—")
    win.btn_connect.click()
    assert wait(lambda: dm.window is not None and dm.window.video.has_frame)
    dm.window.close()
    assert wait(lambda: win.btn_connect.text() == "Connecter" and not dm.active)


def test_unplug_closes_display_with_message_and_replug_resumes(app_with_display, wait):
    win, phone, dm, servers, launchers = app_with_display
    servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=400, fps=30).start())
    phone.set_devices(PHONE)
    assert wait(lambda: win._fields["model"].text() != "—")
    win.btn_connect.click()
    assert wait(lambda: dm.window is not None and dm.window.video.has_frame)
    phone.set_devices()                                         # câble débranché
    assert wait(lambda: not dm.active)
    assert "Téléphone débranché" in win.preview_msg.text()
    assert win.btn_connect.text() == "Déconnecter"              # session toujours active : reprise automatique
    servers.append(FakeServer(sizes=[(180, 360)], frames_per_size=400, fps=30).start())
    phone.set_devices(PHONE)                                    # rebranché
    assert wait(lambda: dm.window is not None and dm.window.video.has_frame)
    assert len(launchers) == 2


def test_launch_failure_is_explained_in_main_window(app_with_display, wait):
    win, phone, dm, servers, launchers = app_with_display

    def failing(serial, settings):
        return ScreenSession(None, serial, settings, launcher_factory=lambda: FakeLauncher(fail="Impossible d'envoyer le serveur."))
    dm._session_factory = failing
    phone.set_devices(PHONE)
    assert wait(lambda: win._fields["model"].text() != "—")
    win.btn_connect.click()
    assert wait(lambda: "Impossible d'envoyer le serveur." in win.preview_msg.text())
    assert wait(lambda: "Déconnecter » puis « Connecter" in win.preview_msg.text())
    assert not dm.active and win.btn_connect.text() == "Déconnecter"
    win.btn_connect.click()
    assert wait(lambda: win.btn_connect.text() == "Connecter")


def test_old_android_does_not_start_display(app_with_display, wait):
    win, phone, dm, servers, launchers = app_with_display
    old = dict(PHONE, release="7", sdk="24")
    phone.set_devices(old)
    assert wait(lambda: win._fields["model"].text() != "—")
    win.btn_connect.click()
    assert wait(lambda: "trop ancienne" in win.preview_msg.text())
    assert not dm.active and not launchers


def test_closing_main_window_stops_everything(phone, qapp, tmp_path, wait):
    store = SettingsStore(tmp_path / "ui.json").load()
    ctl = ConnectionController(AdbClient(phone.cmd), interval_ms=50)
    srv = FakeServer(sizes=[(180, 360)], frames_per_size=400, fps=30).start()
    la = FakeLauncher(srv)
    dm = DisplayManager(store, ctl.adb,
                        session_factory=lambda s, st: ScreenSession(None, s, st, launcher_factory=lambda: la))
    win = MainWindow(store, ctl, auto_wizard=False, display=dm)
    win.show()
    ctl.start()
    phone.set_devices(PHONE)
    assert wait(lambda: win._fields["model"].text() != "—")
    win.btn_connect.click()
    assert wait(lambda: dm.window is not None and dm.window.video.has_frame)
    win.close()
    assert not dm.active and la.cleaned == 1 and srv.quit_received
    assert la.started == 1                                       # pas de redémarrage pendant la fermeture
    assert ctl.shutdown(8000)
    srv.close()


def test_selftest_runs_and_reports_everything(tmp_path, monkeypatch):
    from andromirror import app
    from andromirror.connection.adb import AdbClient as Real
    monkeypatch.setattr(Real, "available", property(lambda self: True))     # pas d'adb embarqué en développement
    monkeypatch.setattr(Real, "version", lambda self: "37.0.1")
    out = tmp_path / "selftest.txt"
    code = app.selftest(str(out))
    text = out.read_text(encoding="utf-8")
    assert code == 0 and text.startswith("OK"), text
    for key in ("adb=", "fenetre=ok", "pyav=", "decodage ok", "affichage=ok", "serveur="):
        assert key in text, text


def test_window_replaces_invalid_opengl_widget_by_software_and_keeps_frame(qapp, monkeypatch):
    """Windows sans carte graphique / OpenGL cassé : le widget OpenGL invalide est remplacé, sans perdre l'image."""
    from andromirror.video.widget import GLVideoWidget, SoftwareVideoWidget
    monkeypatch.setattr(GLVideoWidget, "isValid", lambda self: False)
    win = DisplayWindow("Test")
    win.show()
    qapp.processEvents()
    if not isinstance(win.video, GLVideoWidget):
        pytest.skip("ANDROMIRROR_NO_GL ou création OpenGL impossible : déjà en rendu logiciel")
    from synth import pattern
    import time as _t
    from andromirror.video.decoder import DecodedFrame
    img = pattern(64, 128).convert("RGB")
    win.video.set_frame(DecodedFrame(64, 128, 64 * 3, img.tobytes(), 0, 64, 128, _t.monotonic(), _t.monotonic()))
    targets = []
    win.target_changed.connect(lambda w, h: targets.append((w, h)))
    assert win.ensure_valid_gl() is True
    assert isinstance(win.video, SoftwareVideoWidget) and win.video.has_frame and targets
    assert win.ensure_valid_gl() is False                      # déjà logiciel : rien à faire
    win.close_programmatically()


# --------------------------------------------------------------------------- contrôle (souris, clavier, boutons)
def test_toolbar_buttons_send_keys_once_a_controller_is_plugged(qapp):
    from andromirror.control.controller import InputController
    from andromirror.video import protocol as P
    win = DisplayWindow("Test", force_software=True)
    sent = []
    ctl = InputController(sent.append)
    win.set_controller(ctl)
    assert all(a.isEnabled() for a in win.nav_actions) and win.act_control.isEnabled()
    win.nav_actions[0].trigger()                                    # ◀ Retour
    assert sent == [P.pack_key(P.KEY_DOWN, 4), P.pack_key(P.KEY_UP, 4)]
    win.act_control.setChecked(False)                               # interrupteur « Contrôle » éteint
    assert not ctl.enabled and not win.video.control_enabled
    assert all(not a.isEnabled() for a in win.nav_actions)
    win.close_programmatically()


def test_mouse_on_the_image_becomes_touch_in_video_pixels(qapp):
    from PySide6.QtCore import QPoint
    from andromirror.control.controller import InputController
    from andromirror.video import protocol as P
    from andromirror.video.decoder import DecodedFrame
    win = DisplayWindow("Test", force_software=True)
    sent = []
    win.set_controller(InputController(sent.append))
    win.resize(500, 700)
    win.show()
    qapp.processEvents()
    v = win.video
    v.set_frame(DecodedFrame(width=100, height=200, stride=300, data=bytes(300 * 200), pts_us=0,
                             native_width=1080, native_height=2400))
    from andromirror.video.widget import fit_rect
    r = fit_rect(v.width(), v.height(), 1080, 2400)
    center = QPoint(int(r.center().x()), int(r.center().y()))
    QTest.mousePress(v, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, pos=center)
    assert len(sent) == 2
    assert sent[0][0] == P.C_TOUCH and sent[0][1] == P.TOUCH_DOWN and sent[1][1] == P.TOUCH_UP
    x = int.from_bytes(sent[0][2:4], "big")
    y = int.from_bytes(sent[0][4:6], "big")
    assert abs(x - 540) <= 6 and abs(y - 1200) <= 6                 # le centre de l'image = le centre du téléphone
    assert sent[0][6:10] == (1080).to_bytes(2, "big") + (2400).to_bytes(2, "big")
    # un clic sur une bande noire (hors de l'image) n'envoie rien
    sent.clear()
    if r.x() > 4:
        QTest.mouseClick(v, Qt.MouseButton.LeftButton, pos=QPoint(1, 5))
        assert sent == []
    win.close_programmatically()


def test_double_click_toggles_fullscreen_only_when_control_is_off(qapp):
    win = DisplayWindow("Test", force_software=True)
    seen = []
    win.video.double_clicked.connect(lambda: seen.append(1))
    win.video.mouseDoubleClickEvent(_FakeMouse())
    assert seen == [1]                                              # sans contrôle : comportement d'origine
    win.close_programmatically()


class _FakeMouse:
    """Suffisant pour mouseDoubleClickEvent quand le contrôle est éteint (l'événement n'est pas lu)."""
    def button(self):
        return None
