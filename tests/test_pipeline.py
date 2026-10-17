"""File de paquets, abandon des vieilles images, latence : logique « faible latence » sans réseau ni Qt."""
import threading
import time

import pytest

import synth
from andromirror.video import protocol as P
from andromirror.video.decoder import DecodedFrame
from andromirror.video.pipeline import DecodeWorker, LatestFrame, PacketQueue


def vid(key=False, config=False, n=0):
    return P.Packet(P.T_VIDEO, (P.F_KEY if key else 0) | (P.F_CONFIG if config else 0), n, b"x%d" % n)


def drain(q):
    out = []
    while True:
        p = q.get(0.01)
        if p is None:
            return out
        out.append(p)


def test_queue_is_fifo_below_limit():
    q = PacketQueue(max_packets=10)
    for n in range(5):
        q.put(vid(key=(n == 0), n=n))
    assert [p.pts_us for p in drain(q)] == [0, 1, 2, 3, 4]
    assert q.dropped_packets == 0 and q.overflows == 0


def test_overflow_drops_waiting_video_and_waits_for_keyframe():
    requests = []
    q = PacketQueue(max_packets=4, request_keyframe=lambda: requests.append(1))
    q.put(vid(config=True))
    q.put(vid(key=True, n=1))
    for n in range(2, 6):
        q.put(vid(n=n))                     # le 5e dépasse la limite
    assert q.overflows == 1 and q.waiting_for_keyframe
    assert len(requests) == 1               # image clé demandée
    for n in range(6, 10):
        q.put(vid(n=n))                     # deltas ignorés tant qu'il n'y a pas d'image clé
    left = drain(q)
    assert [p.is_config for p in left] == [True]            # seule la config a survécu
    q.put(vid(key=True, n=10))
    q.put(vid(n=11))
    assert not q.waiting_for_keyframe
    assert [p.pts_us for p in drain(q)] == [10, 11]
    assert q.dropped_packets >= 8


def test_keyframe_requests_are_rate_limited():
    requests = []
    q = PacketQueue(max_packets=2, request_keyframe=lambda: requests.append(time.monotonic()))
    q.put(vid(key=True, n=0))
    q.put(vid(n=1))
    for n in range(2, 200):
        q.put(vid(n=n))
    assert len(requests) == 1


def test_keyframe_on_overflow_is_kept():
    q = PacketQueue(max_packets=3)
    for n in range(3):
        q.put(vid(key=(n == 0), n=n))
    q.put(vid(key=True, n=3))               # l'image clé qui déborde est conservée
    assert not q.waiting_for_keyframe
    assert [p.pts_us for p in drain(q)] == [3]


def test_size_events_and_messages_are_never_dropped():
    q = PacketQueue(max_packets=3)
    q.put(vid(key=True, n=0))
    q.put(P.Packet(P.T_MESSAGE, 0, 0, b"info"))
    q.put(vid(n=1))
    q.put(vid(n=2))
    q.put(vid(n=3))                         # débordement
    left = drain(q)
    assert any(p.type == P.T_MESSAGE for p in left)
    q.put(vid(key=True, n=4))
    q.put(P.Packet(P.T_SIZE, 0, 0, P.pack_size(640, 320, 1)[16:]))
    left = drain(q)
    assert left[-1].type == P.T_SIZE                         # la taille passe, les images d'avant sont obsolètes
    assert not q.waiting_for_keyframe


def test_size_event_resets_keyframe_wait():
    q = PacketQueue(max_packets=2)
    q.put(vid(key=True))
    q.put(vid(n=1))
    q.put(vid(n=2))
    assert q.waiting_for_keyframe
    q.put(P.Packet(P.T_SIZE, 0, 0, P.pack_size(640, 320, 1)[16:]))
    assert not q.waiting_for_keyframe


def test_closed_queue_wakes_consumer():
    q = PacketQueue()
    got = []
    t = threading.Thread(target=lambda: got.append(q.get(5)))
    t.start()
    time.sleep(0.05)
    q.close()
    t.join(1)
    assert got == [None] and not t.is_alive()
    q.put(vid(key=True))        # sans effet
    assert len(q) == 0


def frame(i):
    return DecodedFrame(2, 2, 6, b"\x00" * 12, i, 2, 2, time.monotonic(), time.monotonic())


def test_latest_frame_keeps_only_newest():
    lf = LatestFrame()
    assert lf.put(frame(1)) is True            # case vide : il faut prévenir l'affichage
    assert lf.put(frame(2)) is False
    assert lf.put(frame(3)) is False
    assert lf.replaced == 2 and lf.published == 3
    assert lf.take().pts_us == 3 and lf.take() is None
    assert lf.put(frame(4)) is True


class Collector:
    def __init__(self):
        self.sizes, self.messages, self.errors, self.decoders, self.frames_signalled = [], [], [], [], 0

    def frame(self):
        self.frames_signalled += 1


def run_worker(chunks_by_stage, collector=None, **kw):
    """chunks_by_stage : liste de ('size', w, h) ou de Chunk. Retourne (worker, latest, collector)."""
    q = PacketQueue(max_packets=10_000)
    latest = LatestFrame()
    c = collector or Collector()
    w = DecodeWorker(q, latest, "h264", prefer_hw=False, on_frame=c.frame,
                     on_size=lambda *a: c.sizes.append(a), on_message=lambda *a: c.messages.append(a),
                     on_decoder=c.decoders.append, on_error=c.errors.append, **kw)
    w.start()
    for item in chunks_by_stage:
        if isinstance(item, tuple):
            q.put(P.Packet(P.T_SIZE, 0, 0, P.pack_size(*item)[16:], time.monotonic()))
        elif isinstance(item, P.Packet):
            q.put(item)
        else:
            q.put(P.Packet(P.T_VIDEO, item.flags, item.pts_us, item.data, time.monotonic()))
    return w, latest, c, q


def wait_for(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_worker_decodes_stream_with_config_merge_and_size_change():
    portrait = synth.encode("h264", 320, 640, 10, keyint=5)
    landscape = synth.encode("h264", 640, 320, 10, keyint=5)
    w, latest, c, q = run_worker(portrait)
    assert wait_for(lambda: w.stats.frames_decoded == 10)
    q.put(P.Packet(P.T_SIZE, 0, 0, P.pack_size(640, 320, 1)[16:], time.monotonic()))
    for ch in landscape:
        q.put(P.Packet(P.T_VIDEO, ch.flags, ch.pts_us, ch.data, time.monotonic()))
    assert wait_for(lambda: w.stats.frames_decoded == 20)
    assert c.sizes == [(640, 320, 1)]
    last = latest.take()
    assert (last.native_width, last.native_height) == (640, 320)
    assert w.stats.fps > 0 and w.stats.latency_ms >= 0 and w.stats.decoder == "logiciel"
    assert c.decoders == ["logiciel"] and not c.errors
    w.stop()
    w.join(2)


def test_worker_forwards_messages():
    w, latest, c, q = run_worker([P.Packet(P.T_MESSAGE, 0, 0, "Bonjour".encode()),
                                  P.Packet(P.T_MESSAGE, P.F_FATAL, 0, "Panne".encode())])
    assert wait_for(lambda: len(c.messages) == 2)
    assert c.messages == [("Bonjour", False), ("Panne", True)]
    w.stop()


def test_slow_consumer_only_sees_newest_frame():
    """L'interface n'affiche rien pendant 30 images : elle ne reçoit que la dernière (pas de file qui grossit)."""
    stream = synth.encode("h264", 320, 640, 30, keyint=10)
    w, latest, c, q = run_worker(stream)
    assert wait_for(lambda: w.stats.frames_decoded == 30)
    assert c.frames_signalled == 1                       # un seul « image prête » tant que rien n'est consommé
    assert latest.replaced == 29
    f = latest.take()
    assert f.pts_us == stream[-1].pts_us
    assert w.stats.frames_replaced == 29
    w.stop()


def test_overloaded_decoder_drops_old_packets_and_requests_keyframe_then_recovers():
    requests = []
    q = PacketQueue(max_packets=6, request_keyframe=lambda: requests.append(1))
    latest = LatestFrame()
    gate = threading.Event()

    class SlowDecoder:
        hw_name = None
        description = "lent"

        def __init__(self, *a, **k):
            from andromirror.video.decoder import VideoDecoder
            self.inner = VideoDecoder("h264", prefer_hw=False)

        def decode(self, data, pts, rx):
            gate.wait(5)
            return self.inner.decode(data, pts, rx)

        def reset(self):
            self.inner.reset()

        def close(self):
            pass

    w = DecodeWorker(q, latest, "h264", decoder_factory=SlowDecoder)
    w.start()
    stream = synth.encode("h264", 320, 640, 40, keyint=8)
    for ch in stream[:25]:                               # le décodeur est bloqué : la file déborde
        q.put(P.Packet(P.T_VIDEO, ch.flags, ch.pts_us, ch.data, time.monotonic()))
    assert q.overflows >= 1 and requests
    gate.set()
    for ch in stream[25:]:                               # le flux continue ; il reprend à la prochaine image clé
        q.put(P.Packet(P.T_VIDEO, ch.flags, ch.pts_us, ch.data, time.monotonic()))
        time.sleep(0.005)
    assert wait_for(lambda: latest.published >= 1 and not q.waiting_for_keyframe)
    assert wait_for(lambda: w.stats.frames_decoded >= 5)
    assert w.stats.frames_decoded < 40                   # des images ont bien été abandonnées
    assert w.stats.packets_dropped > 0
    w.stop()


def test_worker_reports_decoder_failure():
    def broken(*a, **k):
        raise RuntimeError("pas de FFmpeg")
    q, latest, errs = PacketQueue(), LatestFrame(), []
    w = DecodeWorker(q, latest, "h264", decoder_factory=broken, on_error=errs.append)
    w.start()
    w.join(2)
    assert errs and "pas de FFmpeg" in errs[0]


def test_latency_between_reception_and_display_is_small():
    stream = synth.encode("h264", 640, 1280, 20, keyint=10)
    w, latest, c, q = run_worker(stream)
    assert wait_for(lambda: w.stats.frames_decoded == 20)
    assert w.stats.latency_ms < 1000          # large marge : machine de test partagée ; on vérifie surtout l'ordre de grandeur
    w.stop()


def test_size_change_drops_stale_frames_still_waiting():
    """Au changement d'orientation, les images de l'ancienne orientation encore en attente sont abandonnées."""
    q = PacketQueue(max_packets=100)
    for n in range(5):
        q.put(vid(key=(n == 0), n=n))
    q.put(P.Packet(P.T_SIZE, 0, 0, P.pack_size(640, 320, 1)[16:]))
    assert [p.type for p in drain(q)] == [P.T_SIZE]
