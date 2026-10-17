"""Réception du flux envoyé par le serveur Android (socket TCP local fourni par `adb forward`).

StreamReceiver ne dépend pas de Qt : il appelle des fonctions que lui donne l'appelant.
Il tourne dans le thread qui appelle run() (jamais dans le thread de l'interface).
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..logging_setup import get_logger
from . import protocol as P

log = get_logger("recepteur")


@dataclass(frozen=True)
class EndInfo:
    """Pourquoi la réception s'est terminée."""
    reason: str                 # "stopped" | "connection_lost" | "fatal_message" | "protocol" | "connect_failed"
    message: str = ""           # texte français pour l'utilisateur / le journal


class StreamReceiver:
    def __init__(self, port: int, token: bytes, on_hello: Callable[[P.Hello], None],
                 on_packet: Callable[[P.Packet], None], host: str = "127.0.0.1",
                 server_alive: Optional[Callable[[], bool]] = None,
                 connect_timeout: float = 20.0):
        if len(token) != P.TOKEN_SIZE:
            raise ValueError("jeton invalide")
        self.host, self.port, self.token = host, port, token
        self._on_hello, self._on_packet = on_hello, on_packet
        self._server_alive = server_alive or (lambda: True)
        self._connect_timeout = connect_timeout
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self.hello: Optional[P.Hello] = None
        self.bytes_received = 0

    # -- contrôle (depuis n'importe quel thread) ------------------------------------------
    def stop(self) -> None:
        """Demande l'arrêt : prévient le serveur (best effort) et coupe le socket."""
        self._stop.set()
        self._send(bytes([P.C_QUIT]))
        self._close()

    def request_keyframe(self) -> None:
        self._send(bytes([P.C_REQUEST_KEYFRAME]))

    def send_command(self, data: bytes) -> None:
        """Envoie une commande complète au téléphone (contrôle : P.pack_touch, pack_key...). Sans effet si déconnecté."""
        if not self._stop.is_set():
            self._send(data)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            with self._send_lock:
                sock.sendall(data)
        except OSError:
            pass

    def _close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # -- connexion ----------------------------------------------------------------------------
    def _connect(self) -> Optional[P.Hello]:
        """Se connecte avec réessais : `adb forward` accepte la connexion avant que le serveur écoute."""
        deadline = time.monotonic() + self._connect_timeout
        last = "délai dépassé"
        while not self._stop.is_set() and time.monotonic() < deadline:
            sock = None
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2.0)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(self.token)
                sock.settimeout(2.0)
                data = P.read_exact(sock, P.HELLO_SIZE, self._stop.is_set,
                                    deadline=min(deadline, time.monotonic() + 8.0))
                hello = P.parse_hello(data)
                sock.settimeout(0.5)     # lectures courtes : on peut vérifier l'arrêt régulièrement
                self._sock = sock
                return hello
            except P.ProtocolError:
                if sock is not None:
                    sock.close()
                raise
            except (P.ConnectionClosed, OSError) as exc:
                last = str(exc) or exc.__class__.__name__
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if not self._server_alive():
                return None
            time.sleep(0.15)
        log.warning("Connexion au serveur impossible : %s", last)
        return None

    # -- boucle ----------------------------------------------------------------------------------
    def run(self) -> EndInfo:
        try:
            hello = self._connect()
        except P.ProtocolError as exc:
            return EndInfo("protocol", f"Réponse inattendue du téléphone : {exc}")
        if hello is None:
            if self._stop.is_set():
                return EndInfo("stopped")
            return EndInfo("connect_failed", "Le serveur du téléphone ne répond pas.")
        self.hello = hello
        log.info("Serveur connecté : %s, %s %dx%d", hello.device_name, hello.codec, hello.width, hello.height)
        try:
            self._on_hello(hello)
            while not self._stop.is_set():
                head = P.read_exact(self._sock, P.HEADER_SIZE, self._stop.is_set)
                ptype, flags, pts, length = P.parse_header(head)
                payload = P.read_exact(self._sock, length, self._stop.is_set) if length else b""
                self.bytes_received += P.HEADER_SIZE + length
                pkt = P.Packet(ptype, flags, pts, payload, time.monotonic())
                if ptype == P.T_MESSAGE and flags & P.F_FATAL:
                    text = payload.decode("utf-8", errors="replace")
                    self._on_packet(pkt)
                    return EndInfo("fatal_message", text)
                self._on_packet(pkt)
        except P.ConnectionClosed as exc:
            if self._stop.is_set():
                return EndInfo("stopped")
            log.info("Connexion perdue : %s", exc)
            return EndInfo("connection_lost", "La connexion avec le téléphone a été coupée.")
        except P.ProtocolError as exc:
            log.error("Flux invalide : %s", exc)
            return EndInfo("protocol", f"Le flux reçu du téléphone est invalide : {exc}")
        finally:
            self._close()
        return EndInfo("stopped")
