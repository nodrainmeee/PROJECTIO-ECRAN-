"""Faux serveur Android pour les tests : parle le même protocole que server_android, via un socket TCP local.

Il joue le rôle du téléphone : vérifie le jeton, envoie le « bonjour », des changements de taille et des images
synthétiques (synth.py), et enregistre les commandes reçues (images clés demandées, arrêt).
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from typing import List, Optional, Sequence, Tuple

import synth
from andromirror.video import protocol as P


class FakeServer:
    def __init__(self, sizes: Sequence[Tuple[int, int]] = ((320, 640),), codec: str = "h264",
                 frames_per_size: int = 20, fps: int = 60, expected_token: Optional[bytes] = None,
                 name: str = "Fake Phone", bad_magic: bool = False, fatal_message: str = "",
                 drop_after: Optional[int] = None, stall_after: Optional[int] = None,
                 keep_open: bool = True, refuse_first: int = 0, keyint: int = 10, audio_chunks: int = 0):
        self.sizes, self.codec, self.fps = list(sizes), codec, fps
        self.frames_per_size = frames_per_size
        self.expected_token = expected_token
        self.name, self.bad_magic, self.fatal_message = name, bad_magic, fatal_message
        self.drop_after, self.stall_after = drop_after, stall_after   # coupure brutale / blocage après N paquets
        self.keep_open = keep_open          # reste connecté après le dernier paquet (comme un vrai téléphone)
        self.refuse_first = refuse_first    # ferme tout de suite les N premières connexions (serveur pas prêt)
        self.keyint = keyint
        self.audio_chunks = audio_chunks        # nombre de paquets de son de 20 ms envoyés après le « bonjour »
        self.commands: List[bytes] = []         # commandes de contrôle reçues (touch, scroll, key, text), complètes
        self.bad_commands = 0
        self.received_token: Optional[bytes] = None
        self.keyframe_requests = 0
        self.quit_received = False
        self.client_closed = threading.Event()
        self.sent_packets = 0
        self.connections = 0
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.token_ok = threading.Event()

    def start(self) -> "FakeServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    # -- serveur ------------------------------------------------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            if self.connections <= self.refuse_first:
                conn.close()
                continue
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self.client_closed.set()

    def _read_commands(self, conn: socket.socket) -> None:
        """Lit les commandes du PC comme le vrai serveur : un octet de type puis un corps de taille connue."""
        conn.settimeout(0.2)
        buf = bytearray()
        while not self._stop.is_set() and not self.client_closed.is_set():
            try:
                b = conn.recv(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if not b:
                break
            buf += b
            while buf:
                t = buf[0]
                if t == P.C_TEXT:
                    if len(buf) < 3:
                        break
                    size = 3 + struct.unpack(">H", bytes(buf[1:3]))[0]
                elif t in P.COMMAND_BODY_SIZE:
                    size = 1 + P.COMMAND_BODY_SIZE[t]
                else:
                    self.bad_commands += 1          # désynchronisation : le vrai serveur arrête la lecture
                    buf.clear()
                    break
                if len(buf) < size:
                    break
                cmd = bytes(buf[:size])
                del buf[:size]
                if t == P.C_REQUEST_KEYFRAME:
                    self.keyframe_requests += 1
                elif t == P.C_QUIT:
                    self.quit_received = True
                else:
                    self.commands.append(cmd)
        self.client_closed.set()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(3)
        tok = b""
        while len(tok) < P.TOKEN_SIZE:
            part = conn.recv(P.TOKEN_SIZE - len(tok))
            if not part:
                return
            tok += part
        self.received_token = tok
        if self.expected_token is not None and tok != self.expected_token:
            return
        self.token_ok.set()
        first = self.sizes[0]
        if self.bad_magic:
            conn.sendall(b"XXXX" + bytes(P.HELLO_SIZE - 4))
            return
        conn.sendall(P.pack_hello(self.name, self.codec, first[0], first[1], 0))
        threading.Thread(target=self._read_commands, args=(conn,), daemon=True).start()
        if self.audio_chunks:
            conn.sendall(P.pack_audio_config(48000, 2))
            pcm = struct.pack("<hh", 1000, -1000) * 960         # 20 ms de signal
            for i in range(self.audio_chunks):
                conn.sendall(P.pack_audio(i * 20000, pcm))
        sent = 0
        for idx, (w, h) in enumerate(self.sizes):
            if idx > 0:
                conn.sendall(P.pack_size(w, h, 1 if w > h else 0))
            for c in synth.encode(self.codec, w, h, self.frames_per_size, fps=self.fps, keyint=self.keyint):
                if self._stop.is_set() or self.client_closed.is_set():
                    return
                if self.drop_after is not None and sent >= self.drop_after:
                    return                                   # coupure brutale
                if self.stall_after is not None and sent >= self.stall_after:
                    while not self._stop.is_set() and not self.client_closed.is_set():
                        time.sleep(0.05)
                    return
                conn.sendall(P.pack_packet(P.T_VIDEO, c.flags, c.pts_us, c.data))
                sent += 1
                self.sent_packets = sent
                time.sleep(1.0 / self.fps / 2)
        if self.fatal_message:
            conn.sendall(P.pack_packet(P.T_MESSAGE, P.F_FATAL, 0, self.fatal_message.encode("utf-8")))
            return
        while self.keep_open and not self._stop.is_set() and not self.client_closed.is_set():
            time.sleep(0.05)
