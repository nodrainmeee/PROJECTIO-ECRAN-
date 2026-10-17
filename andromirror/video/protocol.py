"""Protocole entre le serveur Android et le PC (miroir exact de server_android/.../Wire.java).

Tous les entiers sont en gros-boutiste (« big endian »).

Le PC se connecte, envoie le jeton secret (16 octets), puis lit :
  * le « bonjour » (84 octets) : "AMV1", nom (64), codec (4), largeur, hauteur, rotation (3 × u32) ;
  * ensuite des paquets : en-tête de 16 octets (type u8, indicateurs u8, réservé u16, pts u64, longueur u32)
    suivi des données.
Le PC peut envoyer des commandes d'un octet : C_REQUEST_KEYFRAME, C_QUIT.
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

F_CONFIG = 1     # T_VIDEO : paramètres du codec (SPS/PPS...)
F_KEY = 2        # T_VIDEO : image clé
F_FATAL = 1      # T_MESSAGE : erreur fatale

C_REQUEST_KEYFRAME = 1
C_QUIT = 2

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
    if ptype not in (T_VIDEO, T_SIZE, T_MESSAGE):
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
