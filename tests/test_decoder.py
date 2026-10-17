"""Décodage H.264 / H.265 avec PyAV, sur des flux synthétiques encodés pendant le test."""
import pytest

import synth
from andromirror.video import decoder as D
from andromirror.video.decoder import DecoderError, VideoDecoder, fit_size


def decode_all(dec, chunks):
    cfg, frames = b"", []
    for c in chunks:
        if c.flags & 1:
            cfg = c.data
            continue
        frames += dec.decode((cfg + c.data) if c.flags & 2 else c.data, c.pts_us)
    return frames


@pytest.mark.parametrize("codec,size", [("h264", (320, 640)), ("h264", (640, 320)), ("hevc", (320, 240))])
def test_decode_synthetic_stream_gives_every_frame_with_right_size(codec, size):
    chunks = synth.encode(codec, *size, n=14, keyint=5)
    frames = decode_all(VideoDecoder(codec, prefer_hw=False), chunks)
    assert len(frames) == 14                                   # une image par paquet : pas de retard de décodage
    assert all((f.native_width, f.native_height) == size for f in frames)
    assert all((f.width, f.height) == size for f in frames)
    assert len(frames[0].data) == frames[0].stride * frames[0].height >= size[0] * size[1] * 3


def test_decoded_colors_are_the_test_pattern():
    """La barre blanche (à gauche) et la rouge doivent se retrouver dans l'image décodée."""
    w, h = 320, 640
    frames = decode_all(VideoDecoder("h264", prefer_hw=False), synth.encode("h264", w, h, 3))
    f = frames[-1]

    def px(x, y):
        o = y * f.stride + x * 3
        return tuple(f.data[o:o + 3])
    bw = w / 7
    white = px(int(bw * 0.5), 100)
    red = px(int(bw * 5.5), 100)
    blue = px(int(bw * 6.5), 100)
    assert all(c > 200 for c in white)
    assert red[0] > 180 and red[1] < 70 and red[2] < 70
    assert blue[2] > 180 and blue[0] < 70


def test_output_is_downscaled_to_target_but_aspect_kept():
    chunks = synth.encode("h264", 640, 1280, 4)
    dec = VideoDecoder("h264", prefer_hw=False, target_size=lambda: (200, 200))
    frames = decode_all(dec, chunks)
    f = frames[-1]
    assert (f.native_width, f.native_height) == (640, 1280)
    assert f.height <= 200 and f.width <= 200 and abs(f.width / f.height - 0.5) < 0.02


def test_fit_size_rules():
    assert fit_size((1080, 2400), None) == (1080, 2400)
    assert fit_size((1080, 2400), (0, 0)) == (1080, 2400)
    assert fit_size((1080, 2400), (4000, 4000)) == (1080, 2400)            # jamais agrandie
    w, h = fit_size((1080, 2400), (500, 500))
    assert h == 500 and w == 224 and w % 2 == 0 and h % 2 == 0
    assert fit_size((2400, 1080), (1200, 2000)) == (1200, 540)
    assert min(fit_size((100, 100), (1, 1))) >= 2


def test_reset_on_size_change_decodes_new_size():
    dec = VideoDecoder("h264", prefer_hw=False)
    a = decode_all(dec, synth.encode("h264", 320, 640, 4))
    dec.reset()
    b = decode_all(dec, synth.encode("h264", 640, 320, 4))
    assert (a[-1].native_width, a[-1].native_height) == (320, 640)
    assert (b[-1].native_width, b[-1].native_height) == (640, 320)


def test_corrupt_data_is_ignored_then_recovers_on_next_keyframe():
    dec = VideoDecoder("h264", prefer_hw=False)
    chunks = synth.encode("h264", 320, 640, 12, keyint=4)
    cfg = chunks[0].data
    assert dec.decode(b"\x00\x00\x00\x01garbage-not-h264" * 3) == []      # ne lève rien
    assert dec.decode(b"") == []                               # un paquet vide ne casse pas le décodeur
    frames = []
    for c in chunks[1:]:
        frames += dec.decode((cfg + c.data) if c.flags & 2 else c.data)
    assert len(frames) >= 10


def test_missing_keyframe_start_gives_frames_only_from_next_keyframe():
    dec = VideoDecoder("h264", prefer_hw=False)
    chunks = synth.encode("h264", 320, 640, 13, keyint=4)
    cfg = chunks[0].data
    # on saute la première image clé et ses deltas : rien d'affichable avant la clé suivante
    later = [c for c in chunks[1:]][4:]
    frames, first_key_seen = [], False
    for c in later:
        if c.flags & 2:
            first_key_seen = True
        if not first_key_seen:
            continue
        frames += dec.decode((cfg + c.data) if c.flags & 2 else c.data)
    assert frames


def test_unsupported_codec_and_missing_pyav(monkeypatch):
    with pytest.raises(DecoderError):
        VideoDecoder("vp9")
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "av":
            raise ImportError("pas d'av")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(DecoderError):
        VideoDecoder("h264")


def test_hardware_failure_falls_back_to_software(monkeypatch):
    """Accélération annoncée mais qui plante au décodage : on bascule en logiciel, sans exception."""
    monkeypatch.setattr(D, "hw_candidates", lambda: ["fakehw"])
    real_make = VideoDecoder._make_context
    broken = {}

    class BrokenCtx:
        is_hwaccel = True

        def decode(self, pkt):
            raise RuntimeError("périphérique perdu")

    def make(self, hw_name):
        if hw_name:
            broken["ctx"] = BrokenCtx()
            return broken["ctx"]
        return real_make(self, None)
    monkeypatch.setattr(VideoDecoder, "_make_context", make)
    dec = VideoDecoder("h264", prefer_hw=True)
    assert dec.hw_name == "fakehw" and "matériel" in dec.description
    chunks = synth.encode("h264", 320, 640, 6, keyint=3)
    cfg = chunks[0].data
    assert dec.decode(cfg + chunks[1].data) == []          # l'échec est absorbé
    assert dec.hw_name is None and dec.description == "logiciel"
    frames = []
    for c in chunks[2:]:
        if c.flags & 2:
            frames += dec.decode(cfg + c.data)
        elif frames:
            frames += dec.decode(c.data)
    assert frames
    # et on ne réessaie pas le matériel après un échec
    dec.reset()
    assert dec.hw_name is None


def test_hardware_not_really_active_is_reported_as_software(monkeypatch):
    monkeypatch.setattr(D, "hw_candidates", lambda: ["qsv"])
    dec = VideoDecoder("h264", prefer_hw=True)       # sur cette machine FFmpeg n'active pas qsv
    assert dec.hw_name is None or dec._ctx.is_hwaccel


def test_hardware_disabled_by_setting_and_env(monkeypatch):
    monkeypatch.setattr(D, "hw_candidates", lambda: pytest.fail("ne doit pas être appelé"))
    assert VideoDecoder("h264", prefer_hw=False).hw_name is None
    monkeypatch.setenv("ANDROMIRROR_NO_HW", "1")
    assert VideoDecoder("h264", prefer_hw=True).hw_name is None
