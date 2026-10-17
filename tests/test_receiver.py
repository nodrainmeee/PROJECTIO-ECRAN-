"""Récepteur réseau face à un faux serveur Android."""
import threading
import time

import pytest

from andromirror.video import protocol as P
from andromirror.video.receiver import StreamReceiver
from fake_server import FakeServer

TOKEN = bytes(range(16))


def run_receiver(server, **kw):
    hellos, packets = [], []
    rx = StreamReceiver(server.port, kw.pop("token", TOKEN), hellos.append, packets.append,
                        connect_timeout=kw.pop("connect_timeout", 5), **kw)
    result = []
    t = threading.Thread(target=lambda: result.append(rx.run()), daemon=True)
    t.start()
    return rx, t, result, hellos, packets


def wait_for(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_token_is_sent_first_and_stream_is_received():
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=8, expected_token=TOKEN).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    assert wait_for(lambda: len([p for p in packets if p.type == P.T_VIDEO]) >= 9)    # config + 8 images
    assert srv.received_token == TOKEN
    assert hellos[0].device_name == "Fake Phone" and (hellos[0].width, hellos[0].height) == (160, 320)
    assert packets[0].is_config and packets[1].is_key
    rx.stop()
    t.join(3)
    assert result[0].reason == "stopped"
    assert srv.client_closed.wait(2) and srv.quit_received          # le serveur est prévenu de l'arrêt
    srv.close()


def test_wrong_token_is_rejected_by_server_and_receiver_reports_failure():
    srv = FakeServer(expected_token=TOKEN).start()
    rx, t, result, hellos, packets = run_receiver(srv, token=b"\x01" * 16, connect_timeout=1.0)
    t.join(5)
    assert result[0].reason == "connect_failed" and not hellos
    srv.close()


def test_retries_until_server_is_ready():
    """`adb forward` accepte la connexion avant que le serveur écoute : le PC réessaie."""
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=3, refuse_first=3, expected_token=TOKEN).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    assert wait_for(lambda: hellos)
    assert srv.connections >= 4
    rx.stop()
    t.join(3)
    srv.close()


def test_stops_retrying_when_server_process_died():
    srv = FakeServer(refuse_first=10_000).start()
    start = time.time()
    rx, t, result, hellos, packets = run_receiver(srv, server_alive=lambda: False, connect_timeout=20)
    t.join(5)
    assert result[0].reason == "connect_failed" and time.time() - start < 3
    srv.close()


def test_bad_magic_is_a_protocol_error():
    srv = FakeServer(bad_magic=True).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    t.join(5)
    assert result[0].reason == "protocol" and "signature" in result[0].message
    srv.close()


def test_abrupt_disconnect_is_reported_as_connection_lost():
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=30, drop_after=6).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    t.join(5)
    assert result[0].reason == "connection_lost"
    assert "coupée" in result[0].message
    assert len(packets) == 6
    srv.close()


def test_fatal_message_from_server_ends_session_with_its_text():
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=3, fatal_message="Capture impossible", keep_open=False).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    t.join(5)
    assert result[0].reason == "fatal_message" and result[0].message == "Capture impossible"
    assert packets[-1].type == P.T_MESSAGE
    srv.close()


def test_size_events_are_delivered_in_order():
    srv = FakeServer(sizes=[(160, 320), (320, 160)], frames_per_size=4).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    assert wait_for(lambda: any(p.type == P.T_SIZE for p in packets) and len(packets) >= 11)
    idx = [i for i, p in enumerate(packets) if p.type == P.T_SIZE][0]
    assert P.parse_size(packets[idx].payload) == (320, 160, 1)
    assert all(p.type == P.T_VIDEO for p in packets[:idx])
    rx.stop()
    t.join(3)
    srv.close()


def test_stop_while_stalled_returns_quickly():
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=10, stall_after=3).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    assert wait_for(lambda: len(packets) == 3)
    t0 = time.time()
    rx.stop()
    t.join(3)
    assert not t.is_alive() and time.time() - t0 < 2 and result[0].reason == "stopped"
    srv.close()


def test_stop_before_connect_does_not_hang():
    srv = FakeServer(refuse_first=10_000).start()
    rx, t, result, hellos, packets = run_receiver(srv, connect_timeout=30)
    time.sleep(0.2)
    rx.stop()
    t.join(3)
    assert not t.is_alive() and result[0].reason == "stopped"
    srv.close()


def test_request_keyframe_reaches_server():
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=3).start()
    rx, t, result, hellos, packets = run_receiver(srv)
    assert wait_for(lambda: hellos)
    rx.request_keyframe()
    rx.request_keyframe()
    assert wait_for(lambda: srv.keyframe_requests == 2)
    rx.stop()
    t.join(3)
    srv.close()


def test_invalid_token_size_refused():
    with pytest.raises(ValueError):
        StreamReceiver(1, b"short", lambda h: None, lambda p: None)
