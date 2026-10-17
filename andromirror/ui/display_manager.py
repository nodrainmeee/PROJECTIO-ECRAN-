"""Relie la session d'affichage (ScreenSession) à la fenêtre d'affichage et à la fenêtre principale.

La fenêtre principale ne connaît que DisplayManager : start(), stop(), et ses signaux.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..connection.adb import AdbClient
from ..connection.models import DeviceInfo
from ..logging_setup import get_logger
from ..settings import SettingsStore
from ..video import protocol as P
from ..video.receiver import EndInfo
from ..video.session import ScreenSession
from .display_window import DisplayWindow

log = get_logger("affichage")

STATS_INTERVAL_MS = 500

MESSAGES = {
    "connection_lost": "La connexion avec le téléphone a été coupée (câble débranché ou téléphone verrouillé en "
                       "mode de veille profonde). L'affichage a été fermé.",
}


class DisplayManager(QObject):
    note = Signal(str)            # message pour la fenêtre principale (vide = effacer)
    finished = Signal(object)     # EndInfo : la session est terminée et le téléphone nettoyé
    user_closed = Signal()        # l'utilisateur a fermé la fenêtre d'affichage

    def __init__(self, store: SettingsStore, adb: Optional[AdbClient],
                 session_factory: Optional[Callable[..., ScreenSession]] = None,
                 window_factory: Optional[Callable[..., DisplayWindow]] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.store, self.adb = store, adb
        self._session_factory = session_factory or (
            lambda serial, settings: ScreenSession(self.adb, serial, settings))
        self._window_factory = window_factory or (lambda name: DisplayWindow(name))
        self.session: Optional[ScreenSession] = None
        self.window: Optional[DisplayWindow] = None
        self._stop_note: Optional[str] = None
        self._user_stop = False
        self._decoder_text = "—"
        self._hello: Optional[P.Hello] = None
        self._timer = QTimer(self)
        self._timer.setInterval(STATS_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_stats)

    # -- état --------------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        """Vrai tant qu'une session existe (y compris pendant son arrêt)."""
        return self.session is not None

    # -- démarrage ----------------------------------------------------------------------------------------
    def start(self, device: DeviceInfo) -> None:
        if self.session is not None:
            return
        self._stop_note = None
        self._user_stop = False
        self._hello = None
        self._decoder_text = "—"
        name = device.display_name
        settings = self.store.as_dict()
        sess = self._session_factory(device.serial, settings)
        win = self._window_factory(name)
        self.session, self.window = sess, win
        win.closed_by_user.connect(self._on_window_closed)
        win.target_changed.connect(sess.set_target_size)
        sess.progress.connect(self._on_progress)
        sess.hello.connect(self._on_hello)
        sess.video_size.connect(self._on_size)
        sess.frame_ready.connect(self._on_frame)
        sess.decoder_info.connect(self._on_decoder)
        sess.server_message.connect(self._on_server_message)
        sess.ended.connect(self._on_ended)
        win.show()
        win.ensure_valid_gl()
        QTimer.singleShot(800, win.ensure_valid_gl)
        sess.set_target_size(*win.video.target_pixels)
        self.note.emit("Démarrage de l'affichage…")
        self._timer.start()
        sess.start()

    # -- arrêt --------------------------------------------------------------------------------------------------
    def stop(self, message: str = "", wait: bool = False) -> None:
        """Arrêt voulu (Déconnecter, téléphone débranché, fermeture). `message` : texte affiché ensuite."""
        if self.session is None:
            return
        self._user_stop = True
        self._stop_note = message
        if self.window is not None:
            self.window.show_message("Arrêt de l'affichage…")
        self.session.stop(wait=wait)

    def shutdown(self) -> None:
        """Fermeture de l'application : arrêt complet, nettoyage du téléphone compris."""
        sess = self.session
        if sess is None:
            return
        self._user_stop = True
        self._stop_note = ""
        sess.stop(wait=True, timeout=8.0)
        # les signaux émis depuis le thread de session sont en file : on les traite maintenant
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        if self.session is not None:
            self._release()

    def _on_window_closed(self) -> None:
        log.info("Fenêtre d'affichage fermée par l'utilisateur")
        if self.session is not None:
            self._user_stop = True
            self._stop_note = ""
            self.session.stop()
        self.user_closed.emit()

    # -- événements de la session ----------------------------------------------------------------------
    def _on_progress(self, text: str) -> None:
        if self.window is not None and not self.window.video.has_frame:
            self.window.show_message(text)
        self.note.emit(text)

    def _on_hello(self, h: P.Hello) -> None:
        self._hello = h
        if self.window is not None:
            self.window.show_message("En attente de la première image…")
            self.window.fit_to_video(h.width, h.height, force=True)
        self.note.emit(f"Affichage de {h.device_name or 'votre téléphone'} en cours.")

    def _on_size(self, w: int, h: int, rotation: int) -> None:
        if self.window is not None:
            self.window.fit_to_video(w, h)
        self._refresh_stats()

    def _on_frame(self) -> None:
        sess, win = self.session, self.window
        if sess is None or win is None:
            return
        frame = sess.take_frame()
        if frame is not None:
            win.video.set_frame(frame)

    def _on_decoder(self, text: str) -> None:
        self._decoder_text = text

    def _on_server_message(self, text: str, fatal: bool) -> None:
        log.info("Message du téléphone (fatal=%s) : %s", fatal, text)
        if not fatal:
            self.note.emit(text)

    def _refresh_stats(self) -> None:
        sess, win = self.session, self.window
        if sess is None or win is None:
            return
        st = sess.stats()
        codec = {"h264": "H.264", "hevc": "H.265"}.get(self._hello.codec, "") if self._hello else ""
        size = f"{st.width}×{st.height}" if st.width else "—"
        parts = [size, codec, f"décodage {self._decoder_text}", f"{st.fps:.0f} i/s"]
        if st.latency_ms:
            parts.append(f"délai de décodage {st.latency_ms:.0f} ms")
        if st.packets_dropped:
            parts.append(f"{st.packets_dropped} paquets ignorés")
        win.set_info("  ·  ".join(p for p in parts if p))

    def _on_ended(self, info: EndInfo) -> None:
        log.info("Session terminée : %s", info.reason)
        user_stop = self._user_stop
        stop_note = self._stop_note
        self._release()
        if user_stop or info.reason == "stopped":
            self.note.emit(stop_note or "")
        else:
            self.note.emit(MESSAGES.get(info.reason) or info.message or "L'affichage s'est arrêté.")
        self.finished.emit(info)

    def _release(self) -> None:
        self._timer.stop()
        win, sess = self.window, self.session
        self.window = self.session = None
        if sess is not None:
            for sig in (sess.progress, sess.hello, sess.video_size, sess.frame_ready, sess.decoder_info,
                        sess.server_message, sess.ended):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            sess.deleteLater()
        if win is not None:
            try:
                win.closed_by_user.disconnect()
            except (RuntimeError, TypeError):
                pass
            win.close_programmatically()
            win.deleteLater()
