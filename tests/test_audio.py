"""Son du téléphone : lecteur (file d'attente, volume, rattrapage) et réception dans la session."""
import struct

import pytest

from andromirror.audio.player import MAX_MS, PREFILL_MS, TARGET_MS, AudioPlayer
from andromirror.video.protocol import AudioFormat

FMT = AudioFormat(48000, 2, 16)
CHUNK_20MS = struct.pack("<hh", 1000, -1000) * 960          # 3840 octets = 20 ms stéréo


class FakeStream:
    def __init__(self):
        self.stopped = self.closed = False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def make_player(volume=100):
    box = {}

    def factory(fmt, callback):
        box["fmt"], box["callback"], box["stream"] = fmt, callback, FakeStream()
        return box["stream"]
    return AudioPlayer(volume, stream_factory=factory), box


def pull(box, frames=960):
    out = bytearray(frames * 4)
    box["callback"](out, frames, None, None)
    return bytes(out)


def test_silence_until_prefilled_then_exact_samples():
    p, box = make_player()
    assert p.start(FMT) and p.active
    assert pull(box) == bytes(3840)                       # rien reçu : silence
    for _ in range(PREFILL_MS // 20 + 1):
        p.push(CHUNK_20MS)
    assert pull(box) == CHUNK_20MS                        # assez de données : lecture fidèle


def test_underrun_gives_silence_then_waits_for_prefill_again():
    p, box = make_player()
    p.start(FMT)
    for _ in range(PREFILL_MS // 20 + 1):
        p.push(CHUNK_20MS)
    while p.buffered_ms() >= 20:
        pull(box)
    assert pull(box) == bytes(3840)                       # file vide : silence
    assert p.underruns == 1
    p.push(CHUNK_20MS)                                    # un seul paquet ne relance pas la lecture
    assert pull(box) == bytes(3840)


def test_backlog_is_dropped_to_stay_close_to_live():
    p, box = make_player()
    p.start(FMT)
    for _ in range(MAX_MS // 20 * 3):
        p.push(CHUNK_20MS)
    assert p.buffered_ms() <= MAX_MS
    assert p.dropped_bytes > 0
    assert p.buffered_ms() >= TARGET_MS - 20


def test_partial_frames_are_never_queued():
    p, box = make_player()
    p.start(FMT)
    p.push(CHUNK_20MS + b"\x01\x02\x03")                  # 3 octets en trop : une trame incomplète
    assert p.bytes_pushed == len(CHUNK_20MS)


def test_volume_curve_and_clipping():
    p, box = make_player(volume=50)
    assert p.volume_gain == pytest.approx(0.25)
    p.start(FMT)
    loud = struct.pack("<hh", 20000, -20000) * 960
    for _ in range(PREFILL_MS // 20 + 1):
        p.push(loud)
    left, right = struct.unpack("<hh", pull(box)[:4])
    assert (left, right) == (5000, -5000)
    p.set_volume(0)
    p.push(loud)
    assert p.volume_gain == 0.0
    p.set_volume(100)
    assert p.volume_gain == 1.0


def test_start_failure_is_reported_not_raised():
    def broken(fmt, callback):
        raise OSError("aucune carte son")
    p = AudioPlayer(100, stream_factory=broken)
    assert p.start(FMT) is False
    assert "carte son" in p.error and not p.active
    p.push(CHUNK_20MS)                                    # sans effet, sans erreur
    assert p.bytes_pushed == 0


def test_missing_library_gives_a_clear_message():
    def missing(fmt, callback):
        raise ImportError("sounddevice")
    p = AudioPlayer(100, stream_factory=missing)
    assert p.start(FMT) is False and "composant" in p.error


def test_stop_closes_the_stream_and_clears_the_queue():
    p, box = make_player()
    p.start(FMT)
    p.push(CHUNK_20MS)
    p.stop()
    assert box["stream"].stopped and box["stream"].closed and p.buffered_ms() == 0 and not p.active


# --------------------------------------------------------------------------- dans la session (faux serveur)
def _session_with_audio(server, factory, settings=None):
    from andromirror.settings import defaults
    from andromirror.video.session import ScreenSession
    from test_display import FakeLauncher, Spy
    la = FakeLauncher(server)
    s = ScreenSession(None, "SER", settings or defaults(), launcher_factory=lambda: la, audio_factory=factory)
    return s, Spy(s)


@pytest.mark.usefixtures("qapp")
def test_session_plays_audio_packets(qapp):
    from fake_server import FakeServer
    from test_display import pump
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=6, audio_chunks=10).start()
    opened = []
    players = []

    def factory(volume):
        p = AudioPlayer(volume, stream_factory=lambda fmt, cb: (opened.append(fmt), FakeStream())[1])
        players.append(p)
        return p
    s, spy = _session_with_audio(srv, factory)
    s.start()
    assert pump(qapp, lambda: players and players[0].bytes_pushed >= 10 * len(CHUNK_20MS))
    assert opened == [FMT] and s.audio_active
    assert pump(qapp, lambda: s.stats().frames_decoded >= 3)          # l'image continue pendant le son
    s.stop(wait=True)
    srv.close()


@pytest.mark.usefixtures("qapp")
def test_session_keeps_video_when_audio_output_fails(qapp):
    from fake_server import FakeServer
    from test_display import pump

    def broken(fmt, callback):
        raise OSError("carte son occupée")
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=8, audio_chunks=5).start()
    s, spy = _session_with_audio(srv, lambda v: AudioPlayer(v, stream_factory=broken))
    s.start()
    assert pump(qapp, lambda: any("sortie audio" in m for m, _ in spy.messages))
    assert pump(qapp, lambda: s.stats().frames_decoded >= 3)
    assert not s.audio_active
    s.stop(wait=True)
    srv.close()


@pytest.mark.usefixtures("qapp")
def test_no_audio_player_when_forwarding_is_off(qapp):
    from andromirror.settings import defaults
    from fake_server import FakeServer
    from test_display import pump
    cfg = defaults()
    cfg["audio"]["forward_audio"] = False
    created = []
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=5, audio_chunks=3).start()
    s, spy = _session_with_audio(srv, lambda v: created.append(v), cfg)
    s.start()
    assert pump(qapp, lambda: s.stats().frames_decoded >= 3)
    assert created == [] and not s.audio_active
    s.stop(wait=True)
    srv.close()
