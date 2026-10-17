"""Envoi, lancement et nettoyage du serveur Android (étape 2).

Étapes (toutes par adb, sur le téléphone branché en USB) :
  1. adb push   : copie andromirror-server.jar dans /data/local/tmp (nom unique par session) ;
  2. adb forward: relie un port local du PC (127.0.0.1) au socket local abstrait du serveur ;
  3. adb shell  : lance `app_process` avec le jar (identité « shell ») et des arguments clé=valeur ;
  4. nettoyage  : arrête le processus, retire le « forward » et supprime le jar du téléphone.

Le jeton secret (16 octets) change à chaque session : sans lui, aucune autre application du téléphone
ni aucun autre programme du PC ne peut recevoir l'image.
"""
from __future__ import annotations

import collections
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from .. import paths
from ..logging_setup import get_logger
from .adb import AdbClient

log = get_logger("serveur")

REMOTE_DIR = "/data/local/tmp"
SERVER_CLASS = "com.andromirror.server.Server"
JAR_NAME = "andromirror-server.jar"


class LaunchError(Exception):
    """Échec de l'envoi ou du lancement du serveur (message en français)."""


def find_server_jar() -> Optional[Path]:
    """Le jar embarqué (figé : dossier server/ ; développement : server_android/build/)."""
    for rel in (f"server/{JAR_NAME}", f"server_android/build/{JAR_NAME}"):
        p = paths.resource_path(rel)
        if p.is_file():
            return p
    return None


def server_args(settings: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Réglages de qualité de l'utilisateur -> arguments du serveur."""
    q = settings["quality"]
    res = q["resolution"]
    return {
        "codec": q["codec"],
        "bitrate": str(int(q["bitrate_mbps"])),
        "fps": str(int(q["fps"])),
        "max_short": "0" if res == "native" else str(int(res)),
    }


@dataclass
class RunningServer:
    port: int
    token: bytes
    process: Any                       # subprocess.Popen

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


class ServerLauncher:
    def __init__(self, adb: AdbClient, serial: str, settings: Dict[str, Dict[str, Any]],
                 jar: Optional[Path] = None):
        self.adb, self.serial, self.settings = adb, serial, settings
        self.jar = jar or find_server_jar()
        sid = secrets.token_hex(4)
        self.socket_name = f"andromirror_{sid}"
        self.remote_jar = f"{REMOTE_DIR}/andromirror-server-{sid}.jar"
        self.token = secrets.token_bytes(16)
        self.local_spec: Optional[str] = None
        self.process = None
        self._log: Deque[str] = collections.deque(maxlen=40)
        self._pushed = False
        self._reader: Optional[threading.Thread] = None

    @property
    def server_log(self) -> List[str]:
        return list(self._log)

    # -- démarrage -------------------------------------------------------------------------------------
    def start(self) -> RunningServer:
        if self.jar is None or not self.jar.is_file():
            raise LaunchError("Le composant serveur pour le téléphone (andromirror-server.jar) est introuvable. "
                              "Réinstallez AndroMirror.")
        r = self.adb.push(self.serial, str(self.jar), self.remote_jar)
        self._pushed = True      # même en cas d'échec partiel, le nettoyage essaiera de supprimer le fichier
        if not r.ok:
            raise LaunchError(f"Impossible d'envoyer le serveur sur le téléphone. {r.problem()}")
        port, r = self.adb.forward(self.serial, "tcp:0", f"localabstract:{self.socket_name}")
        if port is None:
            raise LaunchError(f"Impossible de relier le téléphone à l'ordinateur. {r.problem()}")
        self.local_spec = f"tcp:{port}"
        args = dict(server_args(self.settings))
        args.update(token=self.token.hex(), socket=self.socket_name, jar=self.remote_jar)
        cmd = ["shell", f"CLASSPATH={self.remote_jar}", "app_process", "/", SERVER_CLASS]
        cmd += [f"{k}={v}" for k, v in args.items()]
        log.info("Lancement du serveur : adb shell ... %s (port %d)", SERVER_CLASS, port)
        proc = self.adb.popen(cmd, serial=self.serial)
        if proc is None:
            raise LaunchError("Impossible de lancer adb.")
        self.process = proc
        self._reader = threading.Thread(target=self._read_output, args=(proc,), daemon=True,
                                        name="andromirror-serveur-log")
        self._reader.start()
        return RunningServer(port=port, token=self.token, process=proc)

    def _read_output(self, proc) -> None:
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._log.append(line)
                    log.info("téléphone : %s", line)
        except (OSError, ValueError):
            pass

    def failure_hint(self) -> str:
        """Dernière ligne utile de la sortie du serveur (pour expliquer un échec)."""
        lines = [l for l in self._log if l.strip()]
        return lines[-1] if lines else ""

    # -- nettoyage ---------------------------------------------------------------------------------------
    def cleanup(self) -> None:
        """Arrête le serveur, retire le forward et supprime le jar du téléphone. Ne lève jamais."""
        proc, self.process = self.process, None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:  # noqa: BLE001
                        proc.kill()
            except OSError:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except OSError:
                pass
        if self.local_spec:
            self.adb.forward_remove(self.serial, self.local_spec)
            self.local_spec = None
        if self._pushed:
            # Le serveur supprime déjà son jar en fin de session ; ceci couvre un arrêt brutal.
            self.adb.run(["shell", "rm", "-f", self.remote_jar], timeout=4, serial=self.serial)
            self._pushed = False
