"""Réglages de l'application : JSON dans le dossier utilisateur, sauvegarde automatique.

Chaque valeur est validée ; une valeur invalide est remplacée par sa valeur par
défaut sans toucher aux autres. Un fichier corrompu est mis de côté
(settings.json.corrompu) et l'application repart avec les valeurs par défaut.
L'écriture est atomique (fichier temporaire puis remplacement).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import paths
from .logging_setup import get_logger

log = get_logger("settings")

SCHEMA_VERSION = 1

RESOLUTIONS = ["native", "2160", "1440", "1080", "720"]
FPS_VALUES = [30, 60]
CODECS = ["h264", "h265"]
THEMES = ["system", "light", "dark"]

# section -> clé -> (valeur par défaut, validateur)
_Spec = Dict[str, Dict[str, tuple]]
SPEC: _Spec = {
    "quality": {
        "resolution": ("native", ("choice", RESOLUTIONS)),
        "bitrate_mbps": (16, ("int", 4, 60)),
        "fps": (60, ("choice", FPS_VALUES)),
        "codec": ("h264", ("choice", CODECS)),
        "hw_decode": (True, ("bool",)),
    },
    "audio": {
        "forward_audio": (True, ("bool",)),
        "mute_phone": (True, ("bool",)),
        "volume": (100, ("int", 0, 100)),
    },
    "general": {
        "theme": ("system", ("choice", THEMES)),
        "auto_reconnect": (True, ("bool",)),
        "keep_screen_off": (False, ("bool",)),
        "wizard_done": (False, ("bool",)),
    },
    "window": {
        "width": (760, ("int", 560, 7680)),
        "height": (640, ("int", 480, 4320)),
    },
}


def _validate(rule: tuple, value: Any) -> Any:
    """Retourne la valeur nettoyée ou lève ValueError."""
    kind = rule[0]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("booléen attendu")
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("entier attendu")
        return max(rule[1], min(rule[2], value))  # borné
    if kind == "choice":
        if isinstance(value, bool) or value not in rule[1]:
            raise ValueError("valeur non autorisée")
        return value
    raise ValueError("règle inconnue")


def defaults() -> Dict[str, Dict[str, Any]]:
    return {s: {k: v[0] for k, v in keys.items()} for s, keys in SPEC.items()}


class SettingsStore:
    def __init__(self, path: Optional[Path] = None, autosave: bool = True):
        self.path = Path(path) if path else paths.settings_path()
        self.autosave = autosave
        self._data = defaults()
        self._listeners: List[Callable[[str, str, Any], None]] = []
        self.last_save_ok = True

    # -- chargement / sauvegarde -------------------------------------------------
    def load(self) -> "SettingsStore":
        self._data = defaults()
        if not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("racine invalide")
        except (OSError, ValueError) as exc:  # JSONDecodeError hérite de ValueError
            log.error("Fichier de réglages illisible (%s) : valeurs par défaut utilisées", exc)
            self._quarantine()
            return self
        for section, keys in SPEC.items():
            src = raw.get(section)
            if not isinstance(src, dict):
                continue
            for key, (default, rule) in keys.items():
                if key not in src:
                    continue
                try:
                    self._data[section][key] = _validate(rule, src[key])
                except ValueError:
                    log.warning("Réglage invalide ignoré : %s.%s = %r", section, key, src[key])
        return self

    def _quarantine(self) -> None:
        try:
            os.replace(self.path, self.path.with_name(self.path.name + ".corrompu"))
        except OSError:
            log.exception("Impossible de mettre de côté le fichier corrompu")

    def save(self) -> bool:
        payload = {"version": SCHEMA_VERSION, **self._data}
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self.last_save_ok = True
            return True
        except OSError:
            log.exception("Impossible d'enregistrer les réglages")
            self.last_save_ok = False
            return False

    # -- accès ---------------------------------------------------------------------
    def get(self, section: str, key: str) -> Any:
        return self._data[section][key]

    def set(self, section: str, key: str, value: Any) -> Any:
        """Valide, applique et enregistre. Lève ValueError si la valeur est invalide."""
        rule = SPEC[section][key][1]
        clean = _validate(rule, value)
        if self._data[section][key] == clean:
            return clean
        self._data[section][key] = clean
        if self.autosave:
            self.save()
        for fn in list(self._listeners):
            try:
                fn(section, key, clean)
            except Exception:
                log.exception("Erreur dans un écouteur de réglages")
        return clean

    def reset_section(self, *sections: str) -> None:
        """Remet des sections aux valeurs par défaut (une seule sauvegarde)."""
        dflt = defaults()
        changed = []
        for s in sections:
            for k, v in dflt[s].items():
                if self._data[s][k] != v:
                    self._data[s][k] = v
                    changed.append((s, k, v))
        if changed and self.autosave:
            self.save()
        for s, k, v in changed:
            for fn in list(self._listeners):
                try:
                    fn(s, k, v)
                except Exception:
                    log.exception("Erreur dans un écouteur de réglages")

    def as_dict(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self._data)

    def subscribe(self, fn: Callable[[str, str, Any], None]) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Callable[[str, str, Any], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)
