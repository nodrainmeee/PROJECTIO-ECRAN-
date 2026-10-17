import os
import subprocess
import sys
import time

import pytest

from andromirror.connection import adb as adbmod
from andromirror.connection.adb import (AdbClient, parse_adb_version, parse_devices, parse_wm_size)
from andromirror.connection.models import DeviceInfo, DeviceState
from conftest import PHONE


# ---- analyse -------------------------------------------------------------------------
def test_parse_devices_all_states():
    out = (
        "* daemon not running; starting now at tcp:5037\n"
        "* daemon started successfully\n"
        "List of devices attached\n"
        "R58M123ABC             device usb:1-1 product:o1sxeea model:SM_G991B device:o1s transport_id:2\n"
        "0123456789ABCDEF       unauthorized usb:1-2 transport_id:3\n"
        "192.168.1.20:5555      offline transport_id:4\n"
        "ZZZ                    recovery usb:1-3 transport_id:5\n"
        "emulator-5554          device product:sdk model:sdk_gphone64 device:g transport_id:6\n"
        "\n"
    )
    e = parse_devices(out)
    assert [x.serial for x in e] == ["R58M123ABC", "0123456789ABCDEF", "192.168.1.20:5555", "ZZZ", "emulator-5554"]
    assert [x.state for x in e] == [DeviceState.READY, DeviceState.UNAUTHORIZED, DeviceState.OFFLINE,
                                    DeviceState.OTHER, DeviceState.READY]
    assert e[0].model == "SM G991B"
    assert e[2].is_wireless and not e[4].is_wireless


def test_parse_devices_no_permissions_and_empty():
    e = parse_devices("List of devices attached\nXYZ  no permissions (user in plugdev group); see [url]\n")
    assert len(e) == 1 and e[0].raw_state == "no permissions" and e[0].state is DeviceState.OTHER
    assert parse_devices("List of devices attached\n\n") == []
    assert parse_devices("") == []
    assert parse_devices("\x00 garbage") == []


def test_parse_wm_size():
    assert parse_wm_size("Physical size: 1080x2400\n") == ((1080, 2400), None)
    assert parse_wm_size("Physical size: 1440x3200\nOverride size: 1080x2400\n") == ((1440, 3200), (1080, 2400))
    assert parse_wm_size("nonsense") == (None, None)


def test_parse_version():
    assert parse_adb_version("Android Debug Bridge version 1.0.41\nVersion 37.0.1-14910828\n") == "37.0.1"
    assert parse_adb_version("rien") == ""


def test_device_info_properties():
    d = DeviceInfo(serial="S", model="SM-G991B", manufacturer="samsung", android_release="13", sdk=33,
                   screen=(1080, 2400))
    assert d.display_name == "Samsung SM-G991B"
    assert d.android_text == "Android 13 (API 33)"
    assert "1080 × 2400" in d.screen_text
    assert d.audio_supported and d.android_supported
    old = DeviceInfo(serial="S", sdk=28)
    assert old.android_supported and not old.audio_supported
    assert not DeviceInfo(serial="S", sdk=24).android_supported


# ---- client ------------------------------------------------------------------------------
def test_run_ok_and_devices(phone):
    phone.set_devices(PHONE, noise=True)
    c = AdbClient(phone.cmd)
    entries, err = c.list_devices()
    assert err is None and len(entries) == 1 and entries[0].state is DeviceState.READY
    assert c.version() == "37.0.1"


def test_device_info_read(phone):
    phone.set_devices({**PHONE, "override": "720x1600"})
    c = AdbClient(phone.cmd)
    info = c.device_info(PHONE["serial"])
    assert info.complete
    assert (info.model, info.manufacturer, info.android_release, info.sdk) == ("SM-G991B", "samsung", "13", 33)
    assert info.screen == (1080, 2400) and info.override_screen == (720, 1600)


def test_device_info_incomplete_when_device_gone(phone):
    phone.set_devices()
    info = AdbClient(phone.cmd).device_info("NOPE", listed_model="Listed")
    assert not info.complete and info.model == "Listed"


def test_utf8_output(phone):
    phone.set_devices({**PHONE, "model": "Téléphone café ✓"})
    info = AdbClient(phone.cmd).device_info(PHONE["serial"])
    assert info.model == "Téléphone café ✓"


def test_adb_not_found():
    c = AdbClient([os.path.join("definitely", "missing", "adb")])
    r = c.run(["devices"])
    assert r.not_found and not r.ok
    entries, err = c.list_devices()
    assert entries == [] and "introuvable" in err


def test_no_adb_configured(monkeypatch):
    monkeypatch.setattr(adbmod, "find_adb", lambda: None)
    c = AdbClient()
    assert not c.available
    assert c.run(["devices"]).not_found


def test_error_exit(phone):
    phone.set_mode("error")
    r = AdbClient(phone.cmd).run(["devices"])
    assert not r.ok and r.returncode == 1 and "cannot connect" in r.problem()


def test_garbage_output_gives_no_devices(phone):
    phone.set_mode("garbage")
    entries, err = AdbClient(phone.cmd).list_devices()
    assert entries == [] and err is None


def test_timeout_is_enforced(phone):
    phone.set_mode("hang")
    c = AdbClient(phone.cmd)
    t0 = time.monotonic()
    r = c.run(["devices"], timeout=0.5)
    assert r.timed_out and not r.ok
    assert time.monotonic() - t0 < 5
    assert "délai" in r.problem()


def test_cancel_interrupts_running_call(phone):
    import threading
    phone.set_mode("hang")
    c = AdbClient(phone.cmd)
    threading.Timer(0.4, c.cancel).start()
    t0 = time.monotonic()
    r = c.run(["devices"], timeout=30)
    assert r.cancelled and time.monotonic() - t0 < 5
    assert c.run(["devices"]).cancelled   # les appels suivants sont refusés


def test_popen_flags(monkeypatch):
    """stdin=DEVNULL toujours ; CREATE_NO_WINDOW sous Windows."""
    seen = {}

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return b"ok", b""

    def fake_popen(cmd, **kw):
        seen.update(kw)
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    r = AdbClient(["adb"]).run(["devices"], serial="S1")
    assert r.ok
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["cmd"] == ["adb", "-s", "S1", "devices"]
    if os.name == "nt":
        assert seen["creationflags"] == adbmod.CREATE_NO_WINDOW


def test_creationflag_value():
    assert adbmod.CREATE_NO_WINDOW == 0x08000000


def test_find_adb_override(monkeypatch):
    monkeypatch.setenv("ANDROMIRROR_ADB", "/x/adb")
    assert adbmod.find_adb() == ["/x/adb"]


def test_find_adb_bundled(monkeypatch, tmp_path):
    monkeypatch.delenv("ANDROMIRROR_ADB", raising=False)
    exe = "adb.exe" if os.name == "nt" else "adb"
    (tmp_path / "platform-tools").mkdir()
    (tmp_path / "platform-tools" / exe).write_text("x")
    monkeypatch.setattr(adbmod.paths, "resource_path", lambda rel: tmp_path / rel)
    assert adbmod.find_adb() == [str(tmp_path / "platform-tools" / exe)]
