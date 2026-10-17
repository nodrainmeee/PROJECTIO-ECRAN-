"""Correspondance touches du PC (codes Qt) -> codes de touches Android. Sans dépendance à Qt : les codes Qt sont
écrits en nombres (ils sont stables : voir la documentation de Qt::Key)."""
from __future__ import annotations

# Codes Android (KeyEvent.KEYCODE_*)
KEYCODE_HOME = 3
KEYCODE_BACK = 4
KEYCODE_DPAD_UP = 19
KEYCODE_DPAD_DOWN = 20
KEYCODE_DPAD_LEFT = 21
KEYCODE_DPAD_RIGHT = 22
KEYCODE_VOLUME_UP = 24
KEYCODE_VOLUME_DOWN = 25
KEYCODE_POWER = 26
KEYCODE_TAB = 61
KEYCODE_ENTER = 66
KEYCODE_DEL = 67           # retour arrière
KEYCODE_MENU = 82
KEYCODE_PAGE_UP = 92
KEYCODE_PAGE_DOWN = 93
KEYCODE_FORWARD_DEL = 112  # Suppr
KEYCODE_MOVE_HOME = 122
KEYCODE_MOVE_END = 123
KEYCODE_INSERT = 124
KEYCODE_APP_SWITCH = 187

# Codes Qt (Qt::Key)
_QT = {
    0x01000001: KEYCODE_TAB,          # Key_Tab
    0x01000003: KEYCODE_DEL,          # Key_Backspace
    0x01000004: KEYCODE_ENTER,        # Key_Return
    0x01000005: KEYCODE_ENTER,        # Key_Enter (pavé numérique)
    0x01000006: KEYCODE_INSERT,       # Key_Insert
    0x01000007: KEYCODE_FORWARD_DEL,  # Key_Delete
    0x01000010: KEYCODE_MOVE_HOME,    # Key_Home
    0x01000011: KEYCODE_MOVE_END,     # Key_End
    0x01000012: KEYCODE_DPAD_LEFT,    # Key_Left
    0x01000013: KEYCODE_DPAD_UP,      # Key_Up
    0x01000014: KEYCODE_DPAD_RIGHT,   # Key_Right
    0x01000015: KEYCODE_DPAD_DOWN,    # Key_Down
    0x01000016: KEYCODE_PAGE_UP,      # Key_PageUp
    0x01000017: KEYCODE_PAGE_DOWN,    # Key_PageDown
    0x01000055: KEYCODE_MENU,         # Key_Menu
}


def android_keycode(qt_key: int) -> int:
    """Code Android d'une touche « spéciale » du PC, ou 0 si elle n'a pas de correspondance."""
    return _QT.get(int(qt_key), 0)


def is_typable(text: str) -> bool:
    """Vrai si `text` est du texte à saisir (caractères imprimables, pas de touche de commande)."""
    return bool(text) and all(ch.isprintable() for ch in text)
