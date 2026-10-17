"""Envoi / lancement / nettoyage du serveur avec le faux adb."""
import re
import time

import pytest

from andromirror.connection import AdbClient
from andromirror.connection.server_launcher import (LaunchError, ServerLauncher, find_server_jar, server_args)
from andromirror.settings import defaults
from conftest import PHONE


@pytest.fixture
def jar(tmp_path):
    p = tmp_path / "andromirror-server.jar"
    p.write_bytes(b"PK-fake")
    return p


def make(phone, jar, **quality):
    s = defaults()
    s["quality"].update(quality)
    return ServerLauncher(AdbClient(phone.cmd), PHONE["serial"], s, jar=jar)


def test_server_args_from_settings():
    s = defaults()
    assert server_args(s) == {"codec": "h264", "bitrate": "16", "fps": "60", "max_short": "0", "audio": "1"}
    s["quality"].update(resolution="1080", bitrate_mbps=40, fps=30, codec="h265")
    assert server_args(s) == {"codec": "h265", "bitrate": "40", "fps": "30", "max_short": "1080", "audio": "1"}
    s["audio"]["forward_audio"] = False
    assert server_args(s)["audio"] == "0"


def test_start_and_cleanup_issue_the_expected_adb_commands(phone, jar):
    phone.set_devices(PHONE, forward_port=45678)
    la = make(phone, jar, resolution="1440", bitrate_mbps=24, fps=30, codec="h265")
    run = la.start()
    assert run.port == 45678 and len(run.token) == 16 and run.alive()
    deadline = time.time() + 3
    while time.time() < deadline and not any("CLASSPATH" in c for c in phone.calls):
        time.sleep(0.02)
    calls = phone.calls
    serial = PHONE["serial"]
    push = next(c for c in calls if " push " in c or c.startswith(f"-s {serial} push"))
    assert re.search(rf"-s {serial} push .*andromirror-server\.jar /data/local/tmp/andromirror-server-[0-9a-f]{{8}}\.jar", push)
    fwd = next(c for c in calls if " forward " in c)
    assert fwd.startswith(f"-s {serial} forward tcp:0 localabstract:andromirror_")
    launch = next(c for c in calls if "CLASSPATH" in c)
    assert "app_process / com.andromirror.server.Server" in launch
    for expected in ("codec=h265", "bitrate=24", "fps=30", "max_short=1440", f"token={run.token.hex()}",
                     f"socket={la.socket_name}", f"jar={la.remote_jar}"):
        assert expected in launch
    assert la.remote_jar in launch and "CLASSPATH=" + la.remote_jar in launch

    # la sortie du serveur est conservée pour expliquer d'éventuels échecs
    deadline = time.time() + 3
    while time.time() < deadline and not la.server_log:
        time.sleep(0.02)
    assert any("démarrage" in l for l in la.server_log)

    la.cleanup()
    assert not run.alive()
    calls = phone.calls
    assert any(f"forward --remove tcp:45678" in c for c in calls)
    assert any(c.endswith(f"shell rm -f {la.remote_jar}") for c in calls)
    la.cleanup()    # idempotent : aucune exception


def test_token_and_names_change_every_session(phone, jar):
    a, b = make(phone, jar), make(phone, jar)
    assert a.token != b.token and a.socket_name != b.socket_name and a.remote_jar != b.remote_jar


def test_missing_jar(phone, tmp_path):
    la = ServerLauncher(AdbClient(phone.cmd), PHONE["serial"], defaults(), jar=tmp_path / "absent.jar")
    with pytest.raises(LaunchError, match="introuvable"):
        la.start()
    la.cleanup()


def test_push_failure_is_explained_and_cleaned(phone, jar):
    phone.set_devices(PHONE, push_fail=True)
    la = make(phone, jar)
    with pytest.raises(LaunchError, match="envoyer le serveur"):
        la.start()
    la.cleanup()
    assert any("rm -f" in c for c in phone.calls)          # on essaie de ne rien laisser sur le téléphone
    assert not any("CLASSPATH" in c for c in phone.calls)


def test_forward_failure(phone, jar):
    phone.set_devices(PHONE, forward_fail=True)
    la = make(phone, jar)
    with pytest.raises(LaunchError, match="relier"):
        la.start()
    la.cleanup()
    assert not any("--remove" in c for c in phone.calls)


def test_server_that_dies_is_visible(phone, jar):
    phone.set_devices(PHONE, server_exit=True)
    la = make(phone, jar)
    run = la.start()
    deadline = time.time() + 5
    while time.time() < deadline and run.alive():
        time.sleep(0.02)
    assert not run.alive()
    assert "aucune méthode de capture" in la.failure_hint()
    la.cleanup()


def test_phone_unplugged_before_start(phone, jar):
    phone.set_devices()
    la = make(phone, jar)
    with pytest.raises(LaunchError):
        la.start()
    la.cleanup()


def test_find_server_jar_in_dev_tree_if_built():
    p = find_server_jar()
    assert p is None or p.name == "andromirror-server.jar"
