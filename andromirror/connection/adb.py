"""Client ADB : exécution sécurisée de adb (embarqué) et analyse de ses sorties.

Règles de robustesse (mêmes que les autres outils ADB du projet) :
  * stdin=DEVNULL : adb ne peut jamais attendre une saisie ;
  * CREATE_NO_WINDOW sous Windows : pas de fenêtre console qui clignote ;
  * délai maximal sur chaque appel, arrêt propre possible (cancel) ;
  * sortie décodée en UTF-8 (caractères invalides remplacés, jamais d'exception) ;
  * `run()` ne lève jamais : toute panne est décrite dans AdbResult.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .. import paths
from ..logging_setup import get_logger
from .models import DeviceEntry, DeviceInfo, DeviceState

log = get_logger("adb")

DEFAULT_TIMEOUT = 8.0
CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- recherche
def find_adb() -> Optional[List[str]]:
    """Retourne la commande adb à utiliser (liste), ou None si introuvable.

    Ordre : variable ANDROMIRROR_ADB (développement/tests), adb embarqué dans
    platform-tools/, puis (hors version figée) adb du PATH.
    """
    override = os.environ.get("ANDROMIRROR_ADB")
    if override:
        return [override]
    exe = "adb.exe" if os.name == "nt" else "adb"
    bundled = paths.resource_path(f"platform-tools/{exe}")
    if bundled.is_file():
        return [str(bundled)]
    if not getattr(sys, "frozen", False):
        found = shutil.which("adb")
        if found:
            return [found]
    return None


# --------------------------------------------------------------------------- résultat
@dataclass
class AdbResult:
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    not_found: bool = False
    error: Optional[str] = None   # panne de lancement (OSError...)

    @property
    def ok(self) -> bool:
        return (self.returncode == 0 and not self.timed_out and not self.cancelled
                and not self.not_found and self.error is None)

    def problem(self) -> str:
        """Description courte (français) de l'échec."""
        if self.not_found:
            return "adb est introuvable."
        if self.timed_out:
            return "adb ne répond pas (délai dépassé)."
        if self.cancelled:
            return "Opération annulée."
        if self.error:
            return f"adb n'a pas pu démarrer : {self.error}"
        text = (self.stderr or self.stdout).strip().splitlines()
        detail = text[-1] if text else f"code {self.returncode}"
        return f"adb a échoué : {detail}"


# --------------------------------------------------------------------------- analyse
_SKIP_PREFIXES = ("*", "list of devices", "adb server", "daemon")
# États documentés d'adb ; toute autre ligne est ignorée (sortie parasite).
_KNOWN_STATES = {"device", "unauthorized", "authorizing", "offline", "connecting", "recovery",
                 "sideload", "bootloader", "rescue", "host", "detached", "no"}


def _map_state(raw: str) -> DeviceState:
    raw = raw.lower()
    if raw == "device":
        return DeviceState.READY
    if raw in ("unauthorized", "authorizing"):
        return DeviceState.UNAUTHORIZED
    if raw in ("offline", "connecting"):
        return DeviceState.OFFLINE
    return DeviceState.OTHER


def parse_devices(output: str) -> List[DeviceEntry]:
    """Analyse la sortie de `adb devices -l` (tolère le bruit du démarrage du serveur)."""
    entries: List[DeviceEntry] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(_SKIP_PREFIXES):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, raw_state = parts[0], parts[1]
        if raw_state not in _KNOWN_STATES:
            continue
        if raw_state == "no":
            if len(parts) > 2 and parts[2] == "permissions":
                raw_state = "no permissions"
            else:
                continue
        props = {}
        for token in parts[2:]:
            if ":" in token:
                k, _, v = token.partition(":")
                props[k] = v
        entries.append(DeviceEntry(
            serial=serial,
            state=_map_state(raw_state),
            raw_state=raw_state,
            model=props.get("model", "").replace("_", " "),
            transport_id=props.get("transport_id", ""),
        ))
    return entries


_SIZE_RE = re.compile(r"(Physical|Override) size:\s*(\d+)\s*x\s*(\d+)", re.IGNORECASE)


def parse_wm_size(output: str) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """Retourne (taille physique, taille modifiée) d'après `wm size`."""
    physical = override = None
    for kind, w, h in _SIZE_RE.findall(output):
        size = (int(w), int(h))
        if kind.lower() == "physical":
            physical = size
        else:
            override = size
    return physical, override


def parse_adb_version(output: str) -> str:
    """Extrait par ex. « 37.0.1 » de `adb version`."""
    m = re.search(r"^Version\s+([0-9][^\s-]*(?:-\S+)?)", output, re.MULTILINE)
    if m:
        return m.group(1).split("-")[0]
    m = re.search(r"Android Debug Bridge version\s+(\S+)", output)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- client
class AdbClient:
    def __init__(self, adb_cmd: Optional[Sequence[str]] = None, default_timeout: float = DEFAULT_TIMEOUT):
        self.cmd: Optional[List[str]] = list(adb_cmd) if adb_cmd else find_adb()
        self.default_timeout = default_timeout
        self._cancel = threading.Event()

    # -- contrôle --------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.cmd is not None

    def cancel(self) -> None:
        """Interrompt l'appel en cours et refuse les suivants (arrêt de l'application)."""
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    # -- exécution -------------------------------------------------------------------
    def run(self, args: Sequence[str], timeout: Optional[float] = None,
            serial: Optional[str] = None) -> AdbResult:
        if self.cmd is None:
            return AdbResult(not_found=True)
        if self._cancel.is_set():
            return AdbResult(cancelled=True)
        timeout = self.default_timeout if timeout is None else timeout
        cmd = list(self.cmd)
        if serial:
            cmd += ["-s", serial]
        cmd += list(args)

        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except FileNotFoundError:
            log.error("adb introuvable : %s", cmd[0])
            return AdbResult(not_found=True)
        except OSError as exc:
            log.error("Lancement d'adb impossible : %s", exc)
            return AdbResult(error=str(exc))

        deadline = time.monotonic() + timeout
        while True:
            if self._cancel.is_set():
                return self._reap(proc, AdbResult(cancelled=True))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("Délai dépassé (%.1f s) : adb %s", timeout, " ".join(args))
                return self._reap(proc, AdbResult(timed_out=True))
            try:
                out, err = proc.communicate(timeout=min(0.1, remaining))
                return AdbResult(returncode=proc.returncode, stdout=_decode(out), stderr=_decode(err))
            except subprocess.TimeoutExpired:
                continue

    @staticmethod
    def _reap(proc: subprocess.Popen, result: AdbResult) -> AdbResult:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=2)
            result.stdout, result.stderr = _decode(out), _decode(err)
        except Exception:
            pass
        return result

    # -- commandes de haut niveau -----------------------------------------------------
    def version(self) -> str:
        r = self.run(["version"], timeout=5)
        return parse_adb_version(r.stdout) if r.ok else ""

    def list_devices(self) -> Tuple[List[DeviceEntry], Optional[str]]:
        """Retourne (appareils, message d'erreur ou None)."""
        r = self.run(["devices", "-l"])
        if not r.ok:
            return [], r.problem()
        return parse_devices(r.stdout), None

    # -- serveur Android (étape 2) ------------------------------------------------------------
    def popen(self, args: Sequence[str], serial: Optional[str] = None) -> Optional[subprocess.Popen]:
        """Lance adb en tâche de fond (sortie standard et erreurs réunies). None si adb est introuvable."""
        if self.cmd is None or self._cancel.is_set():
            return None
        cmd = list(self.cmd)
        if serial:
            cmd += ["-s", serial]
        cmd += list(args)
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            return subprocess.Popen(cmd, **kwargs)
        except OSError as exc:
            log.error("Lancement d'adb impossible : %s", exc)
            return None

    def push(self, serial: str, local: str, remote: str, timeout: float = 30.0) -> AdbResult:
        return self.run(["push", local, remote], timeout=timeout, serial=serial)

    def forward(self, serial: str, local: str, remote: str) -> Tuple[Optional[int], AdbResult]:
        """`adb forward` ; avec « tcp:0 », adb choisit un port libre et l'affiche. Retourne (port, résultat)."""
        r = self.run(["forward", local, remote], serial=serial)
        port = None
        if r.ok:
            m = re.search(r"(\d{2,5})", r.stdout)
            if m:
                port = int(m.group(1))
            elif local.startswith("tcp:") and local[4:].isdigit() and int(local[4:]) > 0:
                port = int(local[4:])
        return port, r

    def forward_remove(self, serial: str, local: str) -> AdbResult:
        return self.run(["forward", "--remove", local], timeout=4, serial=serial)

    def kill_server(self) -> None:
        self.run(["kill-server"], timeout=5)

    def _getprop(self, serial: str, prop: str) -> Optional[str]:
        r = self.run(["shell", "getprop", prop], serial=serial)
        return r.stdout.strip() if r.ok else None

    def device_info(self, serial: str, listed_model: str = "") -> DeviceInfo:
        """Lit modèle, version Android et taille d'écran. `complete` vaut False si une lecture échoue."""
        model = self._getprop(serial, "ro.product.model")
        if self._cancel.is_set():
            return DeviceInfo(serial=serial, model=listed_model, complete=False)
        manufacturer = self._getprop(serial, "ro.product.manufacturer")
        release = self._getprop(serial, "ro.build.version.release")
        sdk_text = self._getprop(serial, "ro.build.version.sdk")
        size_res = self.run(["shell", "wm", "size"], serial=serial)

        sdk = int(sdk_text) if sdk_text and sdk_text.isdigit() else None
        physical = override = None
        if size_res.ok:
            physical, override = parse_wm_size(size_res.stdout)
        complete = all(v is not None for v in (model, release, sdk_text)) and physical is not None
        return DeviceInfo(
            serial=serial,
            model=model or listed_model,
            manufacturer=manufacturer or "",
            android_release=release or "",
            sdk=sdk,
            screen=physical,
            override_screen=override,
            complete=complete,
        )


def _decode(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", errors="replace")
