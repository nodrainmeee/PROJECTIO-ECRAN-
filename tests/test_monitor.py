import time

import pytest
from PySide6.QtCore import QTimer

from andromirror.connection import AdbClient, ConnectionState
from andromirror.connection.monitor import ConnectionController
from conftest import PHONE

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def ctl(phone, qapp):
    made = []

    def make(interval=50, adb_timeout=8.0, auto=True):
        c = ConnectionController(AdbClient(phone.cmd, default_timeout=adb_timeout), interval_ms=interval,
                                 auto_reconnect=auto)
        c.events = []
        c.snapshot_changed.connect(lambda s: c.events.append(s))
        c.device_lost.connect(lambda s: c.events.append(("lost", s)))
        c.device_reconnected.connect(lambda s: c.events.append(("back", s)))
        made.append(c)
        c.start()
        return c

    yield make
    for c in made:
        assert c.shutdown(8000)


def state_is(c, st):
    return lambda: c.last_snapshot.state is st and c.events


def test_no_device(ctl, phone, wait):
    c = ctl()
    assert wait(lambda: c.events)
    s = c.last_snapshot
    assert s.state is ConnectionState.DISCONNECTED and s.device is None and not s.session_active
    assert s.adb_version == "37.0.1"


def test_device_detected_not_connected_until_click(ctl, phone, wait):
    phone.set_devices(PHONE)
    c = ctl()
    assert wait(lambda: c.last_snapshot.device is not None)
    s = c.last_snapshot
    assert s.state is ConnectionState.DISCONNECTED          # pas encore « Connecter »
    assert s.device.model == "SM-G991B" and s.device.screen == (1080, 2400) and s.device.sdk == 33
    c.connect_device()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.CONNECTED)
    assert c.last_snapshot.session_active
    c.disconnect_device()
    assert wait(lambda: not c.last_snapshot.session_active)
    assert c.last_snapshot.state is ConnectionState.DISCONNECTED


def test_unauthorized(ctl, phone, wait):
    phone.set_devices({"serial": "UNAUTH1", "state": "unauthorized"})
    c = ctl()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.UNAUTHORIZED)
    assert "Autoriser" in c.last_snapshot.message
    # l'utilisateur accepte sur le téléphone
    phone.set_devices({**PHONE, "serial": "UNAUTH1"})
    assert wait(lambda: c.last_snapshot.device is not None)


def test_offline_is_error(ctl, phone, wait):
    phone.set_devices({"serial": "OFF1", "state": "offline"})
    c = ctl()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.ERROR)
    assert "hors ligne" in c.last_snapshot.message


def test_adb_failure_is_error_then_recovers(ctl, phone, wait):
    phone.set_mode("error")
    c = ctl()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.ERROR)
    assert not c.last_snapshot.adb_ok
    phone.set_mode("ok")
    phone.set_devices(PHONE)
    assert wait(lambda: c.last_snapshot.device is not None)


def test_adb_missing(qapp, wait):
    c = ConnectionController(AdbClient(["/nope/adb"]), interval_ms=50)
    c.start()
    try:
        assert wait(lambda: c.last_snapshot.state is ConnectionState.ERROR)
        assert not c.last_snapshot.adb_ok and "introuvable" in c.last_snapshot.message
    finally:
        c.shutdown()


def test_unplug_and_auto_reconnect(ctl, phone, wait):
    phone.set_devices(PHONE)
    c = ctl()
    assert wait(lambda: c.last_snapshot.device is not None)
    c.connect_device()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.CONNECTED)

    phone.set_devices()                                       # débranché
    assert wait(lambda: ("lost", PHONE["serial"]) in c.events)
    assert wait(lambda: c.last_snapshot.waiting_reconnect)
    s = c.last_snapshot
    assert s.state is ConnectionState.DISCONNECTED and s.session_active and "Rebranchez" in s.message
    assert c.events.count(("lost", PHONE["serial"])) == 1     # signalé une seule fois

    phone.set_devices(PHONE)                                  # rebranché
    assert wait(lambda: ("back", PHONE["serial"]) in c.events)
    assert wait(lambda: c.last_snapshot.state is ConnectionState.CONNECTED)
    assert not c.last_snapshot.waiting_reconnect


def test_replug_needs_authorization_then_reconnects(ctl, phone, wait):
    phone.set_devices(PHONE)
    c = ctl()
    c.connect_device()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.CONNECTED)
    phone.set_devices()
    assert wait(lambda: c.last_snapshot.waiting_reconnect)
    phone.set_devices({"serial": PHONE["serial"], "state": "unauthorized"})
    assert wait(lambda: c.last_snapshot.state is ConnectionState.UNAUTHORIZED)
    assert c.last_snapshot.waiting_reconnect
    phone.set_devices(PHONE)
    assert wait(lambda: ("back", PHONE["serial"]) in c.events)


def test_no_auto_reconnect_when_disabled(ctl, phone, wait):
    phone.set_devices(PHONE)
    c = ctl(auto=False)
    c.connect_device()
    assert wait(lambda: c.last_snapshot.state is ConnectionState.CONNECTED)
    phone.set_devices()
    assert wait(lambda: not c.last_snapshot.session_active)
    assert "Cliquez sur « Connecter »" in c.last_snapshot.message
    phone.set_devices(PHONE)
    assert wait(lambda: c.last_snapshot.device is not None)
    assert c.last_snapshot.state is ConnectionState.DISCONNECTED   # pas de reconnexion toute seule


def test_connect_before_phone_plugged_in(ctl, phone, wait):
    c = ctl()
    c.connect_device()
    assert wait(lambda: c.last_snapshot.session_active)
    assert "En attente" in c.last_snapshot.message and not c.last_snapshot.waiting_reconnect
    phone.set_devices(PHONE)
    assert wait(lambda: c.last_snapshot.state is ConnectionState.CONNECTED)


def test_ui_thread_never_blocks_when_adb_hangs(ctl, phone, wait, qapp):
    """adb bloqué 60 s : la boucle Qt principale continue de tourner."""
    phone.set_mode("hang")
    c = ctl(adb_timeout=1.5)
    ticks = []
    t = QTimer()
    t.timeout.connect(lambda: ticks.append(time.monotonic()))
    t.start(20)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.0:
        qapp.processEvents()
        time.sleep(0.005)
    t.stop()
    assert len(ticks) >= 20
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.3
    # puis l'erreur de délai est bien rapportée
    assert wait(lambda: c.last_snapshot.state is ConnectionState.ERROR, timeout=6)
    assert "ne répond pas" in c.last_snapshot.message


def test_shutdown_during_hang_is_fast(phone, qapp, wait):
    phone.set_mode("hang")
    c = ConnectionController(AdbClient(phone.cmd, default_timeout=30), interval_ms=50)
    c.start()
    time.sleep(0.5)       # laisse le worker lancer l'appel bloqué
    t0 = time.monotonic()
    assert c.shutdown(8000)
    assert time.monotonic() - t0 < 5


def test_info_cached_not_refetched(ctl, phone, wait):
    phone.set_devices(PHONE)
    c = ctl()
    assert wait(lambda: len(c.events) >= 6)
    props = [l for l in phone.calls if "getprop ro.product.model" in l]
    assert len(props) == 1
