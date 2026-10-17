"""Module connexion : adb embarqué, détection du téléphone, surveillance."""
from .adb import AdbClient, AdbResult, find_adb, parse_devices, parse_wm_size
from .models import (ConnectionSnapshot, ConnectionState, DeviceEntry, DeviceInfo,
                     DeviceState, STATE_LABELS)

__all__ = [
    "AdbClient", "AdbResult", "find_adb", "parse_devices", "parse_wm_size",
    "ConnectionSnapshot", "ConnectionState", "DeviceEntry", "DeviceInfo",
    "DeviceState", "STATE_LABELS",
]
