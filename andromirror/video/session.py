"""Session d'affichage : lance le serveur, reçoit et décode le flux, prévient l'interface par des signaux Qt.

ScreenSession vit dans le thread de l'interface ; le travail (adb, réseau, décodage) se fait dans deux threads
Python. Les signaux sont émis depuis ces threads : Qt les transmet à l'interface de façon sûre.

Fin de session : toujours le signal `ended(EndInfo)` — arrêt voulu, débranchement, erreur du serveur...
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from ..connection.adb import AdbClient
from ..connection.server_launcher import LaunchError, RunningServer, ServerLauncher
from ..logging_setup import get_logger
from . import protocol as P
from .decoder import DecodedFrame
from .pipeline import DecodeWorker, LatestFrame, PacketQueue, Stats
from .receiver import EndInfo, StreamReceiver

log = get_logger("session")


class ScreenSession(QObject):
    progress = Signal(str)                    # étape en cours (français)
    hello = Signal(object)                    # P.Hello : nom, codec, taille initiale
    video_size = Signal(int, int, int)        # largeur, hauteur, rotation (changement d'orientation)
    frame_ready = Signal()                    # une nouvelle image est disponible (take_frame())
    decoder_info = Signal(str)                # « logiciel » / « matériel (d3d11va) »
    server_message = Signal(str, bool)        # texte, fatal
    ended = Signal(object)                    # EndInfo

    def __init__(self, adb: Optional[AdbClient], serial: str, settings: Dict[str, Dict[str, Any]],
                 launcher_factory: Optional[Callable[[], Any]] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.adb, self.serial, self.settings = adb, serial, settings
        self._launcher_factory = launcher_factory or (lambda: ServerLauncher(adb, serial, settings))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._receiver: Optional[StreamReceiver] = None
        self._worker: Optional[DecodeWorker] = None
        self._queue: Optional[PacketQueue] = None
        self._latest = LatestFrame()
        self._target: Tuple[int, int] = (0, 0)
        self._error: Optional[str] = None
        self._end_sent = False
        self._lock = threading.Lock()
        self.running = False

    # -- API (thread de l'interface) -----------------------------------------------------------------------
    def set_target_size(self, width: int, height: int) -> None:
        """Taille (en pixels) de la zone d'affichage : le décodeur y réduit l'image."""
        self._target = (int(width), int(height))

    def start(self) -> None:
        if self._thread is not None:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="andromirror-session", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False, timeout: float = 8.0) -> None:
        """Arrêt propre. `wait` : attend la fin (nettoyage du téléphone compris), jusqu'à `timeout` secondes."""
        self._stop.set()
        rx = self._receiver
        if rx is not None:
            rx.stop()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def take_frame(self) -> Optional[DecodedFrame]:
        return self._latest.take()

    def stats(self) -> Stats:
        if self._worker is not None:
            st = self._worker.stats
            st.frames_replaced = self._latest.replaced
            return st
        return Stats()

    @property
    def bytes_received(self) -> int:
        return self._receiver.bytes_received if self._receiver else 0

    # -- thread de session -----------------------------------------------------------------------------------
    def _target_size(self):
        return self._target if self._target[0] > 0 else None

    def _run(self) -> None:
        launcher = None
        info = EndInfo("stopped")
        try:
            launcher = self._launcher_factory()
            self.progress.emit("Envoi du serveur sur le téléphone…")
            try:
                running: RunningServer = launcher.start()
            except LaunchError as exc:
                self._finish(EndInfo("launch_failed", str(exc)), launcher)
                return
            if self._stop.is_set():
                self._finish(EndInfo("stopped"), launcher)
                return
            self.progress.emit("Démarrage de l'affichage…")
            receiver = StreamReceiver(running.port, running.token, self._on_hello, self._on_packet,
                                      server_alive=running.alive)
            self._receiver = receiver
            if self._stop.is_set():
                receiver.stop()
            info = receiver.run()
            if info.reason == "connect_failed":
                hint = getattr(launcher, "failure_hint", lambda: "")()
                msg = "Le serveur du téléphone n'a pas démarré."
                if hint:
                    msg += f" Dernier message du téléphone : « {hint} »"
                info = EndInfo("connect_failed", msg)
            elif info.reason == "connection_lost" and not running.alive():
                hint = getattr(launcher, "failure_hint", lambda: "")()
                if hint:
                    info = EndInfo("connection_lost", f"{info.message} Dernier message du téléphone : « {hint} »")
            if self._error and info.reason in ("stopped", "connection_lost"):
                info = EndInfo("decoder_failed", self._error)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erreur inattendue dans la session d'affichage")
            info = EndInfo("error", f"Erreur inattendue : {exc}")
        self._finish(info, launcher)

    def _finish(self, info: EndInfo, launcher) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.stop()
        if self._queue is not None:
            self._queue.close()
        if launcher is not None:
            try:
                launcher.cleanup()
            except Exception:  # noqa: BLE001
                log.exception("Nettoyage du téléphone incomplet")
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(2.0)
        self.running = False
        with self._lock:
            if self._end_sent:
                return
            self._end_sent = True
        log.info("Fin de session : %s %s", info.reason, info.message)
        try:
            self.ended.emit(info)
        except RuntimeError:
            pass        # l'objet Qt a déjà été détruit (fermeture de l'application) : plus personne à prévenir

    # -- rappels du récepteur (thread de session) ----------------------------------------------------------
    def _on_hello(self, h: P.Hello) -> None:
        self._queue = PacketQueue(request_keyframe=lambda: self._receiver and self._receiver.request_keyframe())
        prefer_hw = bool(self.settings.get("quality", {}).get("hw_decode", True))
        self._worker = DecodeWorker(
            self._queue, self._latest, h.codec, prefer_hw=prefer_hw, target_size=self._target_size,
            request_keyframe=lambda: self._receiver and self._receiver.request_keyframe(),
            on_frame=self.frame_ready.emit,
            on_size=self.video_size.emit,
            on_message=self.server_message.emit,
            on_decoder=self.decoder_info.emit,
            on_error=self._on_decoder_error)
        self._worker.stats.width, self._worker.stats.height = h.width, h.height
        self._worker.start()
        self.hello.emit(h)

    def _on_packet(self, pkt: P.Packet) -> None:
        if self._queue is not None:
            self._queue.put(pkt)

    def _on_decoder_error(self, message: str) -> None:
        self._error = message
        self.stop()
