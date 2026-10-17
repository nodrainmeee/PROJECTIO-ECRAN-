"""Contrôleur : transforme les actions de l'utilisateur (souris, clavier, boutons) en commandes pour le téléphone.

Ne dépend pas de Qt : il reçoit une fonction `send(bytes)` (le récepteur du flux) et des valeurs simples.
Toutes les méthodes sont sans effet quand `enabled` est faux ou quand le téléphone n'est pas connecté.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional, Set

from ..video import protocol as P
from . import keymap


class InputController:
    def __init__(self, send: Callable[[bytes], None]):
        self._send = send
        self.enabled = True
        self._touching = False
        self._down_keys: Dict[int, int] = {}   # code Qt -> code Android, pour les touches maintenues
        self.sent_commands = 0

    # -- utilitaires ---------------------------------------------------------------------------------------------
    def _emit(self, data: bytes) -> None:
        self.sent_commands += 1
        try:
            self._send(data)
        except Exception:  # noqa: BLE001 - le téléphone peut avoir disparu ; ne jamais faire planter l'interface
            pass

    def set_enabled(self, enabled: bool) -> None:
        """Désactiver relâche tout (doigt levé, touches relâchées) pour ne rien laisser « coincé »."""
        if not enabled and self.enabled:
            self.release_all()
        self.enabled = enabled

    def release_all(self) -> None:
        self._touching = False
        for code in list(self._down_keys.values()):
            self._emit(P.pack_key(P.KEY_UP, code))
        self._down_keys.clear()

    # -- souris (coordonnées en pixels de la vidéo) ---------------------------------------------------------------------
    def touch_down(self, x: int, y: int, vw: int, vh: int) -> None:
        if not self.enabled:
            return
        self._touching = True
        self._emit(P.pack_touch(P.TOUCH_DOWN, x, y, vw, vh))

    def touch_move(self, x: int, y: int, vw: int, vh: int) -> None:
        if self.enabled and self._touching:
            self._emit(P.pack_touch(P.TOUCH_MOVE, x, y, vw, vh))

    def touch_up(self, x: int, y: int, vw: int, vh: int) -> None:
        if self.enabled and self._touching:
            self._touching = False
            self._emit(P.pack_touch(P.TOUCH_UP, x, y, vw, vh))

    @property
    def touching(self) -> bool:
        return self._touching

    def scroll(self, x: int, y: int, vw: int, vh: int, notches_h: float, notches_v: float) -> None:
        """Molette : nombres de crans (positif = vers le haut / la droite)."""
        if not self.enabled:
            return
        u = P.SCROLL_UNITS_PER_NOTCH
        h, v = round(notches_h * u), round(notches_v * u)
        if h or v:
            self._emit(P.pack_scroll(x, y, vw, vh, h, v))

    # -- touches Android ----------------------------------------------------------------------------------------------------
    def press(self, android_code: int) -> None:
        """Appui bref (boutons Retour, Accueil, Volume...)."""
        if not self.enabled:
            return
        self._emit(P.pack_key(P.KEY_DOWN, android_code))
        self._emit(P.pack_key(P.KEY_UP, android_code))

    def key_down(self, qt_key: int, text: str, ctrl: bool, alt: bool) -> None:
        """Touche du clavier du PC enfoncée. Le texte imprimable est envoyé comme texte ; les touches spéciales
        (Entrée, flèches...) comme touches. Ctrl + lettre (raccourcis du PC) est ignoré ; AltGr (Ctrl+Alt) = texte."""
        if not self.enabled:
            return
        if text and keymap.is_typable(text) and not (ctrl and not alt):
            self.type_text(text)
            return
        code = keymap.android_keycode(qt_key)
        if code:
            self._down_keys[int(qt_key)] = code
            self._emit(P.pack_key(P.KEY_DOWN, code))

    def key_up(self, qt_key: int) -> None:
        code = self._down_keys.pop(int(qt_key), 0)
        if code and self.enabled:
            self._emit(P.pack_key(P.KEY_UP, code))

    def type_text(self, text: str) -> None:
        if self.enabled and text:
            self._emit(P.pack_text(text))
