"""Chemins : ressources embarquées (PyInstaller) et dossier utilisateur."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "AndroMirror"


def project_root() -> Path:
    """Racine du projet (ou dossier temporaire de PyInstaller si figé)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """Chemin d'une ressource embarquée (assets, platform-tools...)."""
    return project_root() / relative


def app_data_dir() -> Path:
    """Dossier utilisateur de l'application (créé si besoin).

    - Variable ANDROMIRROR_HOME : surcharge (tests, usage portable).
    - Windows : %APPDATA%\\AndroMirror
    - Autres systèmes (développement) : ~/.config/AndroMirror
    """
    override = os.environ.get("ANDROMIRROR_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def log_dir() -> Path:
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return log_dir() / "andromirror.log"
