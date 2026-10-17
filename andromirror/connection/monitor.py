"""Surveillance de la connexion dans un thread à part (l'interface ne bloque jamais).

ConnectionWorker vit dans un QThread : il interroge adb à intervalle régulier et
envoie des « photos » (ConnectionSnapshot) à l'interface via des signaux Qt.
ConnectionController est l'objet utilisé côté interface.

Reconnexion (F5, base) : si l'utilisateur est « connecté » et que le téléphone
disparaît, on le signale (device_lost) puis, dès qu'il revient, device_reconnected.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from ..logging_setup import get_logger
from .adb import AdbClient
from .models import ConnectionSnapshot, ConnectionState, DeviceEntry, DeviceInfo, DeviceState

log = get_logger("monitor")

POLL_INTERVAL_MS = 2000
KILL_SERVER_AFTER_FAILURES = 3


class ConnectionWorker(QObject):
    snapshot = Signal(object)           # ConnectionSnapshot
    device_lost = Signal(str)           # numéro de série
    device_reconnected = Signal(str)

    def __init__(self, adb: AdbClient, interval_ms: int = POLL_INTERVAL_MS, auto_reconnect: bool = True):
        super().__init__()
        self._adb = adb
        self._interval = interval_ms
        self._auto_reconnect = auto_reconnect
        self._session = False
        self._timer: Optional[QTimer] = None
        self._stopped = False
        self._cache: Dict[str, DeviceInfo] = {}
        self._connected_serial: Optional[str] = None   # appareil de la session
        self._lost = False
        self._failures = 0
        self._killed_in_streak = False
        self._adb_version = ""

    # -- slots (exécutés dans le thread du worker) --------------------------------
    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._tick()

    @Slot()
    def stop(self) -> None:
        self._stopped = True
        if self._timer:
            self._timer.stop()

    def cleanup(self) -> None:
        """Appelé dans le thread du worker à sa fin : arrête le minuteur (évite un avertissement Qt)."""
        self._stopped = True
        if self._timer:
            self._timer.stop()

    @Slot(bool)
    def set_session(self, active: bool) -> None:
        if active == self._session:
            return
        self._session = active
        if not active:
            self._connected_serial = None
            self._lost = False
        self._tick()

    @Slot(bool)
    def set_auto_reconnect(self, enabled: bool) -> None:
        self._auto_reconnect = enabled

    @Slot()
    def refresh(self) -> None:
        self._tick()

    # -- boucle ------------------------------------------------------------------------
    def _tick(self) -> None:
        if self._stopped:
            return
        if self._timer:
            self._timer.stop()
        try:
            snap = self._compute()
        except Exception:  # un bug ne doit jamais tuer la surveillance
            log.exception("Erreur inattendue pendant la détection")
            snap = ConnectionSnapshot(state=ConnectionState.ERROR,
                                      message="Erreur interne pendant la détection (voir le journal).",
                                      session_active=self._session)
        if self._stopped:
            return
        self.snapshot.emit(snap)
        if self._timer:
            self._timer.start(self._interval)

    def _compute(self) -> ConnectionSnapshot:
        adb = self._adb
        if not adb.available:
            return ConnectionSnapshot(
                state=ConnectionState.ERROR, adb_ok=False, session_active=self._session,
                message="adb est introuvable. Réinstallez AndroMirror (le composant ADB est fourni avec le logiciel).")
        if not self._adb_version:
            self._adb_version = adb.version()

        entries, error = adb.list_devices()
        if error:
            self._failures += 1
            log.warning("Détection impossible (%d de suite) : %s", self._failures, error)
            # Jamais pendant une session dont le téléphone est présent : redémarrer adb coupe l'affichage en cours.
            streaming = self._session and not self._lost
            if self._failures >= KILL_SERVER_AFTER_FAILURES and not self._killed_in_streak and not streaming:
                self._killed_in_streak = True
                log.warning("Redémarrage du serveur adb")
                adb.kill_server()
            return self._snap(ConnectionState.ERROR, f"{error} Nouvel essai automatique en cours.",
                              adb_ok=False)
        self._failures = 0
        self._killed_in_streak = False

        ready = [e for e in entries if e.state is DeviceState.READY]
        unauth = [e for e in entries if e.state is DeviceState.UNAUTHORIZED]
        bad = [e for e in entries if e.state in (DeviceState.OFFLINE, DeviceState.OTHER)]
        entries_t = tuple(entries)

        if ready:
            chosen = next((e for e in ready if e.serial == self._connected_serial), ready[0])
            info = self._info_for(chosen)
            extra = f" ({len(ready)} téléphones détectés, utilisation de celui-ci.)" if len(ready) > 1 else ""
            if self._session:
                if self._lost:
                    self._lost = False
                    log.info("Téléphone reconnecté : %s", chosen.serial)
                    self.device_reconnected.emit(chosen.serial)
                self._connected_serial = chosen.serial
                return self._snap(ConnectionState.CONNECTED,
                                  f"Connecté à {info.display_name}.{extra}", info, entries_t)
            return self._snap(ConnectionState.DISCONNECTED,
                              f"Téléphone détecté : {info.display_name}. Cliquez sur « Connecter ».{extra}",
                              info, entries_t)

        if unauth:
            return self._snap(
                ConnectionState.UNAUTHORIZED,
                "Regardez l'écran du téléphone et touchez « Autoriser » dans la fenêtre "
                "« Autoriser le débogage USB ? ». Cochez « Toujours autoriser » pour ne plus avoir à le refaire.",
                None, entries_t)

        if bad:
            e = bad[0]
            if e.state is DeviceState.OFFLINE:
                msg = ("Le téléphone est détecté mais « hors ligne ». Débranchez puis rebranchez le câble, "
                       "ou désactivez puis réactivez le débogage USB.")
            elif e.raw_state == "no permissions":
                msg = "Windows/ADB n'a pas la permission d'utiliser ce téléphone. Rebranchez-le puis réessayez."
            else:
                msg = (f"Le téléphone est dans un mode non pris en charge (« {e.raw_state} »). "
                       "Redémarrez-le normalement.")
            return self._snap(ConnectionState.ERROR, msg, None, entries_t)

        # Aucun téléphone
        if self._session and self._connected_serial and not self._lost:
            self._lost = True
            lost = self._connected_serial
            log.info("Téléphone perdu : %s", lost)
            self.device_lost.emit(lost)
            if not self._auto_reconnect:
                self._session = False
                self._connected_serial = None
                self._lost = False
                return self._snap(ConnectionState.DISCONNECTED,
                                  "Le téléphone a été débranché. Cliquez sur « Connecter » après l'avoir rebranché.")
        if self._session:
            if self._lost:
                msg = "Téléphone débranché. Rebranchez-le : la connexion reprendra automatiquement."
            else:
                msg = "En attente d'un téléphone… Branchez-le en USB (débogage USB activé)."
            return self._snap(ConnectionState.DISCONNECTED, msg)
        return self._snap(ConnectionState.DISCONNECTED,
                          "Aucun téléphone détecté. Branchez-le en USB, ou ouvrez l'assistant de première utilisation.")

    def _info_for(self, entry: DeviceEntry) -> DeviceInfo:
        cached = self._cache.get(entry.serial)
        if cached is not None:
            return cached
        info = self._adb.device_info(entry.serial, entry.model)
        if info.complete:
            self._cache[entry.serial] = info
        return info

    def _snap(self, state: ConnectionState, message: str, device: Optional[DeviceInfo] = None,
              devices: tuple = (), adb_ok: bool = True) -> ConnectionSnapshot:
        if not self._session:
            # Hors session, on oublie les infos en cache d'un appareil disparu.
            present = {e.serial for e in devices}
            for s in list(self._cache):
                if s not in present:
                    del self._cache[s]
        return ConnectionSnapshot(
            state=state, message=message, device=device, devices=devices,
            session_active=self._session,
            waiting_reconnect=self._session and self._lost,
            adb_ok=adb_ok, adb_version=self._adb_version,
        )


class ConnectionController(QObject):
    """Interface côté fenêtre : signaux Qt, aucune opération bloquante."""
    snapshot_changed = Signal(object)
    device_lost = Signal(str)
    device_reconnected = Signal(str)

    _req_start = Signal()
    _req_stop = Signal()
    _req_session = Signal(bool)
    _req_auto = Signal(bool)
    _req_refresh = Signal()

    def __init__(self, adb: Optional[AdbClient] = None, interval_ms: int = POLL_INTERVAL_MS,
                 auto_reconnect: bool = True, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.adb = adb or AdbClient()
        self.last_snapshot = ConnectionSnapshot()
        self._thread = QThread(self)
        self._thread.setObjectName("andromirror-adb")
        self._worker = ConnectionWorker(self.adb, interval_ms, auto_reconnect)
        self._worker.moveToThread(self._thread)
        self._thread.finished.connect(self._worker.cleanup, Qt.ConnectionType.DirectConnection)

        self._req_start.connect(self._worker.start)
        self._req_stop.connect(self._worker.stop)
        self._req_session.connect(self._worker.set_session)
        self._req_auto.connect(self._worker.set_auto_reconnect)
        self._req_refresh.connect(self._worker.refresh)
        self._worker.snapshot.connect(self._on_snapshot)
        self._worker.device_lost.connect(self.device_lost)
        self._worker.device_reconnected.connect(self.device_reconnected)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        self._req_start.emit()

    def connect_device(self) -> None:
        self._req_session.emit(True)

    def disconnect_device(self) -> None:
        self._req_session.emit(False)

    def refresh(self) -> None:
        self._req_refresh.emit()

    def set_auto_reconnect(self, enabled: bool) -> None:
        self._req_auto.emit(enabled)

    def _on_snapshot(self, snap: ConnectionSnapshot) -> None:
        self.last_snapshot = snap
        self.snapshot_changed.emit(snap)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Arrête le thread proprement (interrompt un appel adb en cours). Retourne True si arrêté."""
        if not self._started:
            return True
        self.adb.cancel()
        self._req_stop.emit()
        self._thread.quit()
        ok = self._thread.wait(timeout_ms)
        if not ok:
            log.error("Le thread adb ne s'est pas arrêté dans le délai imparti")
        self._started = False
        return ok
