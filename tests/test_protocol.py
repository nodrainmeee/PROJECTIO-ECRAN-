"""Format des messages entre le serveur Android et le PC."""
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from andromirror.video import protocol as P

ROOT = Path(__file__).resolve().parent.parent


def test_sizes_match_documentation():
    assert P.HELLO_SIZE == 84 and P.HEADER_SIZE == 16 and P.TOKEN_SIZE == 16


def test_hello_roundtrip():
    h = P.parse_hello(P.pack_hello("Samsung SM-G991B", "h264", 1080, 2400, 1))
    assert (h.device_name, h.codec, h.width, h.height, h.rotation) == ("Samsung SM-G991B", "h264", 1080, 2400, 1)
    h2 = P.parse_hello(P.pack_hello("Pixel é", "h265", 2400, 1080, 3))
    assert h2.codec == "hevc" and h2.device_name == "Pixel é" and h2.rotation == 3


def test_hello_long_name_not_cut_in_middle_of_character():
    h = P.parse_hello(P.pack_hello("é" * 70, "h264", 100, 200))
    assert h.device_name == "é" * 32 and "\ufffd" not in h.device_name


@pytest.mark.parametrize("mutate", [
    lambda b: b"XXXX" + b[4:],                          # mauvaise signature
    lambda b: b[:4] + b[4:68] + b"vp90" + b[72:],       # codec inconnu
    lambda b: b[:-1],                                   # trop court
])
def test_hello_rejects_garbage(mutate):
    with pytest.raises(P.ProtocolError):
        P.parse_hello(mutate(P.pack_hello("x", "h264", 100, 200)))


def test_hello_rejects_absurd_size():
    with pytest.raises(P.ProtocolError):
        P.parse_hello(P.pack_hello("x", "h264", 0, 200))
    with pytest.raises(P.ProtocolError):
        P.parse_hello(P.pack_hello("x", "h264", 100, 99999))


def test_packet_header_roundtrip():
    raw = P.pack_packet(P.T_VIDEO, P.F_KEY, 123456789, b"abc")
    ptype, flags, pts, length = P.parse_header(raw[:16])
    assert (ptype, flags, pts, length) == (P.T_VIDEO, P.F_KEY, 123456789, 3)
    assert raw[16:] == b"abc"


def test_header_rejects_unknown_type_and_huge_length():
    with pytest.raises(P.ProtocolError):
        P.parse_header(P.pack_packet(9, 0, 0, b"")[:16])
    bad = P.HEADER_STRUCT.pack(P.T_VIDEO, 0, 0, 0, P.MAX_PAYLOAD + 1)
    with pytest.raises(P.ProtocolError):
        P.parse_header(bad)
    with pytest.raises(P.ProtocolError):
        P.parse_header(b"short")


def test_packet_flags():
    assert P.Packet(P.T_VIDEO, P.F_CONFIG, 0, b"").is_config
    assert P.Packet(P.T_VIDEO, P.F_KEY, 0, b"").is_key
    assert not P.Packet(P.T_VIDEO, 0, 0, b"").is_key
    assert not P.Packet(P.T_MESSAGE, P.F_FATAL, 0, b"").is_config        # F_FATAL == F_CONFIG (1) : le type compte


def test_size_payload():
    raw = P.pack_size(2400, 1080, 3)
    _, _, _, length = P.parse_header(raw[:16])
    assert P.parse_size(raw[16:16 + length]) == (2400, 1080, 3)
    with pytest.raises(P.ProtocolError):
        P.parse_size(b"123")
    with pytest.raises(P.ProtocolError):
        P.parse_size(P.pack_size(5, 5, 0)[16:])


def _pair():
    a, b = socket.socketpair()
    a.settimeout(0.05)
    return a, b


def test_read_exact_reassembles_pieces():
    a, b = _pair()

    def writer():
        for chunk in (b"he", b"llo ", b"wor", b"ld"):
            b.sendall(chunk)
    threading.Thread(target=writer).start()
    assert P.read_exact(a, 11) == b"hello world"


def test_read_exact_connection_closed():
    a, b = _pair()
    b.sendall(b"ab")
    b.close()
    with pytest.raises(P.ConnectionClosed):
        P.read_exact(a, 5)


def test_read_exact_stop_and_deadline():
    a, b = _pair()
    with pytest.raises(P.ConnectionClosed):
        P.read_exact(a, 5, should_stop=lambda: True)
    import time
    with pytest.raises(P.ConnectionClosed):
        P.read_exact(a, 5, deadline=time.monotonic() + 0.1)


# -- le côté Java produit exactement les mêmes octets -------------------------------------------------------------
@pytest.mark.skipif(not (shutil.which("javac") and shutil.which("java")), reason="JDK absent")
def test_java_wire_matches_python(tmp_path):
    srv = ROOT / "server_android"
    out = tmp_path / "classes"
    out.mkdir()
    subprocess.run(["javac", "-encoding", "UTF-8", "-d", str(out), str(srv / "src/com/andromirror/server/Wire.java"),
                    str(srv / "test/WireSelfTest.java")], check=True, capture_output=True)
    res = subprocess.run(["java", "-cp", str(out), "WireSelfTest"], check=True, capture_output=True, text=True)
    got = dict(line.split(" ", 1) for line in res.stdout.strip().splitlines())
    assert got["HELLO"] == P.pack_hello("Pixel é", "h264", 1080, 2400, 1).hex()
    assert got["HEADER"] == P.HEADER_STRUCT.pack(P.T_VIDEO, P.F_KEY, 0, 123456789, 4242).hex()
    assert got["SIZE"] == P.pack_size(2400, 1080, 3)[16:].hex()
    assert got["LONGNAME"] == P.pack_hello("é" * 70, "h265", 1, 2, 0).hex()
