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
    assert got["AUDIOCFG"] == P.pack_audio_config(48000, 2, 16)[16:].hex()
    assert got["AUDIOHEADER"] == P.pack_audio_config(48000, 2, 16)[:16].hex()


# -- contrôle et son -----------------------------------------------------------------------------------------------
def test_control_commands_have_the_documented_sizes():
    for raw, t in ((P.pack_touch(P.TOUCH_DOWN, 10, 20, 1080, 2400), P.C_TOUCH),
                   (P.pack_scroll(1, 2, 1080, 2400, 0, -16), P.C_SCROLL),
                   (P.pack_key(P.KEY_DOWN, 66, 0), P.C_KEY)):
        assert raw[0] == t and len(raw) == 1 + P.COMMAND_BODY_SIZE[t]


def test_touch_fields_and_clipping():
    raw = P.pack_touch(P.TOUCH_MOVE, 70000, -5, 1080, 2400)      # hors limites : ramené dans u16
    assert raw == bytes([P.C_TOUCH, P.TOUCH_MOVE]) + (0xFFFF).to_bytes(2, "big") + (0).to_bytes(2, "big") \
        + (1080).to_bytes(2, "big") + (2400).to_bytes(2, "big")


def test_scroll_is_signed():
    raw = P.pack_scroll(0, 0, 100, 100, 0, -16)
    assert int.from_bytes(raw[-2:], "big", signed=True) == -16


def test_text_is_length_prefixed_utf8_and_never_cut_in_a_character():
    assert P.pack_text("é") == bytes([P.C_TEXT, 0, 2]) + "é".encode()
    long = P.pack_text("é" * 400)
    n = int.from_bytes(long[1:3], "big")
    assert n <= P.MAX_TEXT_BYTES and len(long) == 3 + n and long[3:].decode("utf-8") == "é" * (n // 2)


def test_audio_config_roundtrip_and_validation():
    raw = P.pack_audio_config(48000, 2)
    ptype, flags, _pts, length = P.parse_header(raw[:16])
    assert ptype == P.T_AUDIO and flags & P.F_CONFIG and length == 8
    fmt = P.parse_audio_config(raw[16:])
    assert (fmt.sample_rate, fmt.channels, fmt.bits, fmt.frame_bytes) == (48000, 2, 16, 4)
    with pytest.raises(P.ProtocolError):
        P.parse_audio_config(P.AUDIO_CONFIG_STRUCT.pack(48000, 6, 16))
    with pytest.raises(P.ProtocolError):
        P.parse_audio_config(b"123")
    pkt = P.Packet(P.T_AUDIO, P.F_CONFIG, 0, b"")
    assert pkt.is_audio and pkt.is_audio_config and not pkt.is_config
