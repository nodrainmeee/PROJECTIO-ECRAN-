"""Modèles de données de la connexion (sans dépendance à Qt)."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional, Tuple

MIN_ANDROID_SDK = 26      # Android 8
AUDIO_MIN_SDK = 30        # Android 11 (transfert audio)


class DeviceState(enum.Enum):
    """État brut d'un appareil tel que rapporté par `adb devices`."""
    READY = "device"
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"
    OTHER = "other"        # recovery, bootloader, sideload, pas de permission...


class ConnectionState(enum.Enum):
    """États affichés à l'utilisateur (F3)."""
    DISCONNECTED = "disconnected"   # non connecté
    UNAUTHORIZED = "unauthorized"   # en attente d'autorisation
    CONNECTED = "connected"         # connecté
    ERROR = "error"                 # erreur


STATE_LABELS = {
    ConnectionState.DISCONNECTED: "Non connecté",
    ConnectionState.UNAUTHORIZED: "En attente d'autorisation",
    ConnectionState.CONNECTED: "Connecté",
    ConnectionState.ERROR: "Erreur",
}


@dataclass(frozen=True)
class DeviceEntry:
    """Une ligne de `adb devices -l`."""
    serial: str
    state: DeviceState
    raw_state: str = ""
    model: str = ""          # modèle tel que listé par adb (underscores -> espaces)
    transport_id: str = ""

    @property
    def is_wireless(self) -> bool:
        return ":" in self.serial and not self.serial.startswith("emulator")


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    model: str = ""
    manufacturer: str = ""
    android_release: str = ""
    sdk: Optional[int] = None
    screen: Optional[Tuple[int, int]] = None           # taille physique (largeur, hauteur)
    override_screen: Optional[Tuple[int, int]] = None  # taille modifiée par l'utilisateur
    complete: bool = True                              # False si une lecture a échoué

    @property
    def display_name(self) -> str:
        name = self.model or self.serial
        if self.manufacturer and self.model and not self.model.lower().startswith(self.manufacturer.lower()):
            return f"{self.manufacturer.capitalize()} {self.model}"
        return name

    @property
    def android_text(self) -> str:
        if not self.android_release:
            return "—"
        return f"Android {self.android_release}" + (f" (API {self.sdk})" if self.sdk else "")

    @property
    def screen_text(self) -> str:
        if not self.screen:
            return "—"
        text = f"{self.screen[0]} × {self.screen[1]} pixels"
        if self.override_screen and self.override_screen != self.screen:
            text += f" (affichage réglé sur {self.override_screen[0]} × {self.override_screen[1]})"
        return text

    @property
    def android_supported(self) -> bool:
        return self.sdk is None or self.sdk >= MIN_ANDROID_SDK

    @property
    def audio_supported(self) -> bool:
        return self.sdk is None or self.sdk >= AUDIO_MIN_SDK


@dataclass(frozen=True)
class ConnectionSnapshot:
    """Photo de l'état de la connexion, envoyée à l'interface."""
    state: ConnectionState = ConnectionState.DISCONNECTED
    message: str = ""
    device: Optional[DeviceInfo] = None          # appareil détecté / connecté
    devices: Tuple[DeviceEntry, ...] = field(default_factory=tuple)
    session_active: bool = False                 # l'utilisateur a cliqué sur « Connecter »
    waiting_reconnect: bool = False              # appareil perdu, reconnexion attendue (F5)
    adb_ok: bool = True
    adb_version: str = ""

    @property
    def title(self) -> str:
        return STATE_LABELS[self.state]

    @property
    def device_detected(self) -> bool:
        return self.device is not None
