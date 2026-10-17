"""Flux vidéo synthétique pour les tests : mire de couleurs encodée en H.264 / H.265 avec PyAV (libx264 / libx265).

Aucun téléphone : on fabrique des paquets au même format que ceux du serveur Android
(Annex B, paramètres SPS/PPS séparés dans un paquet « config », puis une image par paquet).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List

import av
from PIL import Image, ImageDraw

BARS = [(235, 235, 235), (235, 235, 16), (16, 235, 235), (16, 235, 16), (235, 16, 235), (235, 16, 16), (16, 16, 235)]


def pattern(width: int, height: int, i: int = 0) -> Image.Image:
    """Mire : 7 barres de couleur, un carré blanc qui bouge, le texte « AndroMirror L×H »."""
    img = Image.new("RGB", (width, height), (24, 24, 28))
    d = ImageDraw.Draw(img)
    bar_h = height * 2 // 3
    bw = width / len(BARS)
    for k, c in enumerate(BARS):
        d.rectangle([int(k * bw), 0, int((k + 1) * bw) - 1, bar_h], fill=c)
    side = max(16, min(width, height) // 8)
    x = (i * 17) % max(1, width - side)
    d.rectangle([x, bar_h + 8, x + side, bar_h + 8 + side], fill=(255, 255, 255))
    d.text((12, height - 28), f"AndroMirror {width}x{height}  image {i}", fill=(255, 255, 255))
    d.rectangle([0, 0, width - 1, height - 1], outline=(255, 0, 0), width=3)
    return img


@dataclass
class Chunk:
    flags: int          # F_CONFIG / F_KEY comme sur le réseau
    data: bytes
    pts_us: int


def _nals(data: bytes):
    """Découpe un flux Annex B en unités NAL (avec leur préfixe 00 00 01 / 00 00 00 01)."""
    starts, i = [], 0
    while True:
        j = data.find(b"\x00\x00\x01", i)
        if j < 0:
            break
        starts.append(j - 1 if j > 0 and data[j - 1] == 0 else j)
        i = j + 3
    starts.append(len(data))
    return [data[a:b] for a, b in zip(starts, starts[1:])]


def _nal_type(nal: bytes, codec: str) -> int:
    off = 4 if nal[:4] == b"\x00\x00\x00\x01" else 3
    return (nal[off] & 0x1F) if codec == "h264" else ((nal[off] >> 1) & 0x3F)


def split_config(first: bytes, codec: str):
    """Sépare les paramètres (SPS/PPS ou VPS/SPS/PPS) du reste du premier paquet, comme MediaCodec."""
    wanted = {7, 8} if codec == "h264" else {32, 33, 34}
    cfg, rest = [], []
    for n in _nals(first):
        (cfg if _nal_type(n, codec) in wanted else rest).append(n)
    return b"".join(cfg), b"".join(rest)


def encode(codec: str, width: int, height: int, n: int, fps: int = 30, keyint: int = 30,
           start_index: int = 0) -> List[Chunk]:
    """n images encodées. Premier élément : paquet de configuration ; images clés tous les `keyint`."""
    name = {"h264": "libx264", "hevc": "libx265"}[codec]
    enc = av.CodecContext.create(name, "w")
    enc.width, enc.height, enc.pix_fmt = width, height, "yuv420p"
    enc.time_base, enc.framerate = Fraction(1, fps), Fraction(fps, 1)
    enc.gop_size = keyint
    if codec == "h264":
        enc.options = {"preset": "ultrafast", "tune": "zerolatency", "profile": "high", "bf": "0",
                       "x264-params": f"keyint={keyint}:min-keyint={keyint}:scenecut=0:repeat-headers=1"}
    else:
        enc.options = {"preset": "ultrafast", "tune": "zerolatency",
                       "x265-params": f"keyint={keyint}:min-keyint={keyint}:bframes=0:scenecut=0:"
                                      "repeat-headers=1:log-level=none:frame-threads=1"}
    enc.open()
    chunks: List[Chunk] = []
    packets = []
    for i in range(n):
        frame = av.VideoFrame.from_image(pattern(width, height, start_index + i)).reformat(format="yuv420p")
        frame.pts = i
        frame.time_base = Fraction(1, fps)
        packets += list(enc.encode(frame))
    packets += list(enc.encode(None))
    for k, p in enumerate(packets):
        data = bytes(p)
        pts = int(k * 1_000_000 / fps)
        if k == 0:
            cfg, rest = split_config(data, codec)
            chunks.append(Chunk(1, cfg, 0))                 # F_CONFIG
            chunks.append(Chunk(2, rest, pts))              # F_KEY
        else:
            chunks.append(Chunk(2 if p.is_keyframe else 0, data, pts))
    return chunks
