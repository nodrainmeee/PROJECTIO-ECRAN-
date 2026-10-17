"""Protocole entre le serveur Android et le PC (miroir exact de server_android/.../Wire.java).

Tous les entiers sont en gros-boutiste (« big endian »).

Le PC se connecte, envoie le jeton secret (16 octets), puis lit :
  * le « bonjour » (84 octets) : "AMV1", nom (64), codec (4), largeur, hauteur, rotation (3 × u32) ;
  * ensuite des paquets : en-tête de 16 octets (type u8, indicateurs u8, réservé u16, pts u64, longueur u32)
    suivi des données.
Le PC envoie des commandes : un octet de type, suivi d'un corps de taille fixe (voir pack_* plus bas) :
C_REQUEST_KEYFRAME et C_QUIT (sans corps), C_TOUCH, C_SCROLL, C_KEY, C_TEXT.
Le son (paquets T_AUDIO) est du PCM 16 bits : un premier paquet F_CONFIG décrit le format, les suivants le contiennent.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Optional

MAGIC = b"AMV1"
NAME_SIZE = 64
HELLO_STRUCT = struct.Struct(">4s64s4sIII")
HELLO_SIZE = HELLO_STRUCT.size            # 84
HEADER_STRUCT = struct.Struct(">BBHQI")
HEADER_SIZE = HEADER_STRUCT.size          # 16
TOKEN_SIZE = 16

T_VIDEO = 1
T_SIZE = 2
T_MESSAGE = 3
T_AUDIO = 4

F_CONFIG = 1     # T_VIDEO : paramètres du codec (SPS/PPS...)
F_KEY = 2        # T_VIDEO : image clé
F_FATAL = 1      # T_MESSAGE : erreur fatale
# T_AUDIO : avec F_CONFIG, le paquet décrit le format du son (8 octets : fréquence u32, canaux u16, bits u16)

C_REQUEST_KEYFRAME = 1
C_QUIT = 2
C_TOUCH = 3      # action u8, x u16, y u16, largeur u16, hauteur u16          (9 octets de corps)
C_SCROLL = 4     # x u16, y u16, largeur u16, hauteur u16, défil. H i16, V i16 (12 octets)
C_KEY = 5        # action u8, code Android u32, méta u32                        (9 octets)
C_TEXT = 6       # longueur u16, puis texte UTF-8 (au plus MAX_TEXT_BYTES)

TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE = 0, 1, 2
KEY_DOWN, KEY_UP = 0, 1
SCROLL_UNITS_PER_NOTCH = 16      # une « cran » de molette = 16 unités (le serveur divise par 16)
MAX_TEXT_BYTES = 300
AUDIO_CONFIG_STRUCT = struct.Struct(">IHH")   # fréquence (Hz), canaux, bits par échantillon

MAX_PAYLOAD = 64 * 1024 * 1024            # garde-fou : un paquet plus gros est forcément corrompu
CODECS = {b"h264": "h264", b"h265": "hevc"}


class ProtocolError(Exception):
    """Données reçues invalides (flux corrompu ou ce n'est pas notre serveur)."""


class ConnectionClosed(Exception):
    """La connexion a été coupée avant la fin d'un message."""


@dataclass(frozen=True)
class Hello:
    device_name: str
    codec: str          # nom FFmpeg : "h264" ou "hevc"
    width: int
    height: int
    rotation: int


@dataclass(frozen=True)
class Packet:
    type: int
    flags: int
    pts_us: int
    payload: bytes
    received_at: float = 0.0     # time.monotonic() à la réception (mesure de latence)

    @property
    def is_config(self) -> bool:
        return self.type == T_VIDEO and bool(self.flags & F_CONFIG)

    @property
    def is_key(self) -> bool:
        return self.type == T_VIDEO and bool(self.flags & F_KEY)

    @property
    def is_audio(self) -> bool:
        return self.type == T_AUDIO

    @property
    def is_audio_config(self) -> bool:
        return self.type == T_AUDIO and bool(self.flags & F_CONFIG)


# --------------------------------------------------------------------------- analyse
def parse_hello(data: bytes) -> Hello:
    if len(data) != HELLO_SIZE:
        raise ProtocolError(f"« bonjour » de {len(data)} octets au lieu de {HELLO_SIZE}")
    magic, name, codec, w, h, rot = HELLO_STRUCT.unpack(data)
    if magic != MAGIC:
        raise ProtocolError("ce n'est pas le serveur AndroMirror (signature incorrecte)")
    if codec not in CODECS:
        raise ProtocolError(f"codec inconnu : {codec!r}")
    if not (16 <= w <= 16384 and 16 <= h <= 16384):
        raise ProtocolError(f"taille vidéo invalide : {w}×{h}")
    return Hello(name.rstrip(b"\x00").decode("utf-8", errors="replace"), CODECS[codec], w, h, rot % 4)


def parse_header(data: bytes) -> tuple:
    """Retourne (type, indicateurs, pts, longueur)."""
    if len(data) != HEADER_SIZE:
        raise ProtocolError("en-tête incomplet")
    ptype, flags, _reserved, pts, length = HEADER_STRUCT.unpack(data)
    if ptype not in (T_VIDEO, T_SIZE, T_MESSAGE, T_AUDIO):
        raise ProtocolError(f"type de paquet inconnu : {ptype}")
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"paquet trop gros : {length} octets")
    return ptype, flags, pts, length


def parse_size(payload: bytes) -> tuple:
    """Retourne (largeur, hauteur, rotation) d'un paquet T_SIZE."""
    if len(payload) != 12:
        raise ProtocolError("paquet de taille invalide")
    w, h, rot = struct.unpack(">III", payload)
    if not (16 <= w <= 16384 and 16 <= h <= 16384):
        raise ProtocolError(f"taille vidéo invalide : {w}×{h}")
    return w, h, rot % 4


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int
    bits: int

    @property
    def frame_bytes(self) -> int:
        return self.channels * self.bits // 8


def parse_audio_config(payload: bytes) -> AudioFormat:
    if len(payload) != AUDIO_CONFIG_STRUCT.size:
        raise ProtocolError("format audio invalide")
    rate, channels, bits = AUDIO_CONFIG_STRUCT.unpack(payload)
    if not (8000 <= rate <= 192000 and channels in (1, 2) and bits == 16):
        raise ProtocolError(f"format audio non pris en charge : {rate} Hz, {channels} canaux, {bits} bits")
    return AudioFormat(rate, channels, bits)


# --------------------------------------------------------------------------- commandes PC -> téléphone
def _clip16(v: int) -> int:
    return max(0, min(0xFFFF, int(v)))


def _clip_i16(v: int) -> int:
    return max(-0x8000, min(0x7FFF, int(v)))


def pack_touch(action: int, x: int, y: int, width: int, height: int) -> bytes:
    """Appui / déplacement / relâchement ; (x, y) en pixels de la VIDÉO, (width, height) = taille de la vidéo."""
    return struct.pack(">BBHHHH", C_TOUCH, action, _clip16(x), _clip16(y), _clip16(width), _clip16(height))


def pack_scroll(x: int, y: int, width: int, height: int, hscroll: int, vscroll: int) -> bytes:
    """Molette : hscroll/vscroll en unités de 1/16 de cran (positif = vers le haut / la droite)."""
    return struct.pack(">BHHHHhh", C_SCROLL, _clip16(x), _clip16(y), _clip16(width), _clip16(height),
                       _clip_i16(hscroll), _clip_i16(vscroll))


def pack_key(action: int, keycode: int, meta: int = 0) -> bytes:
    return struct.pack(">BBII", C_KEY, action, keycode & 0xFFFFFFFF, meta & 0xFFFFFFFF)


def pack_text(text: str) -> bytes:
    """Texte à saisir. Coupé (sans casser un caractère) à MAX_TEXT_BYTES octets."""
    raw = text.encode("utf-8")
    if len(raw) > MAX_TEXT_BYTES:
        n = MAX_TEXT_BYTES
        while n > 0 and (raw[n] & 0xC0) == 0x80:
            n -= 1
        raw = raw[:n]
    return struct.pack(">BH", C_TEXT, len(raw)) + raw


# Taille du corps (après l'octet de type) des commandes à taille fixe
COMMAND_BODY_SIZE = {C_REQUEST_KEYFRAME: 0, C_QUIT: 0, C_TOUCH: 9, C_SCROLL: 12, C_KEY: 9}


# --------------------------------------------------------------------------- fabrication (tests / faux serveur)
def pack_hello(name: str, codec: str, width: int, height: int, rotation: int = 0) -> bytes:
    full = name.encode("utf-8")
    n = min(len(full), NAME_SIZE)
    while 0 < n < len(full) and (full[n] & 0xC0) == 0x80:
        n -= 1                               # ne pas couper un caractère en deux
    raw = full[:n]
    tag = {"h264": b"h264", "hevc": b"h265", "h265": b"h265"}[codec]
    return HELLO_STRUCT.pack(MAGIC, raw.ljust(NAME_SIZE, b"\x00"), tag, width, height, rotation)


def pack_packet(ptype: int, flags: int, pts_us: int, payload: bytes) -> bytes:
    return HEADER_STRUCT.pack(ptype, flags, 0, pts_us, len(payload)) + payload


def pack_audio_config(sample_rate: int, channels: int, bits: int = 16) -> bytes:
    return pack_packet(T_AUDIO, F_CONFIG, 0, AUDIO_CONFIG_STRUCT.pack(sample_rate, channels, bits))


def pack_audio(pts_us: int, pcm: bytes) -> bytes:
    return pack_packet(T_AUDIO, 0, pts_us, pcm)


def pack_size(width: int, height: int, rotation: int) -> bytes:
    return pack_packet(T_SIZE, 0, 0, struct.pack(">III", width, height, rotation))


# --------------------------------------------------------------------------- lecture
def read_exact(sock, n: int, should_stop: Optional[Callable[[], bool]] = None,
               deadline: Optional[float] = None) -> bytes:
    """Lit exactement n octets. sock a un délai court (socket.timeout = on vérifie should_stop et on réessaie).

    Lève ConnectionClosed si la connexion est coupée, si should_stop() devient vrai, ou si l'échéance
    `deadline` (time.monotonic()) est dépassée.
    """
    import socket as _socket
    import time as _time
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        if should_stop is not None and should_stop():
            raise ConnectionClosed("arrêt demandé")
        if deadline is not None and _time.monotonic() > deadline:
            raise ConnectionClosed("délai dépassé")
        try:
            r = sock.recv_into(view[got:], n - got)
        except _socket.timeout:
            continue
        except (ConnectionError, OSError) as exc:
            raise ConnectionClosed(str(exc)) from exc
        if r == 0:
            raise ConnectionClosed("connexion fermée par le téléphone")
        got += r
    return bytes(buf)
