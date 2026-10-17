"""Thèmes clair / sombre (palette + feuille de style)."""
from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "window": "#f3f5f9", "card": "#ffffff", "border": "#d9dee8", "text": "#1c2230",
        "muted": "#5d6678", "accent": "#2f6fed", "accent_text": "#ffffff", "accent_hover": "#2559c4",
        "button": "#e8ecf4", "button_hover": "#dbe1ee", "preview": "#e4e8f1",
        "ok": "#1f9d55", "warn": "#d98a00", "err": "#d93a3a", "idle": "#8b93a5",
    },
    "dark": {
        "window": "#14171f", "card": "#1d222d", "border": "#2e3544", "text": "#e8ebf2",
        "muted": "#9aa3b6", "accent": "#5b8cff", "accent_text": "#0b1020", "accent_hover": "#7aa2ff",
        "button": "#2a3040", "button_hover": "#353c50", "preview": "#10131a",
        "ok": "#3ccf7a", "warn": "#f0b429", "err": "#ff6b6b", "idle": "#6c7589",
    },
}


def resolve_mode(setting: str, app: QApplication | None = None) -> str:
    """Transforme « system / light / dark » en « light » ou « dark »."""
    if setting in ("light", "dark"):
        return setting
    app = app or QApplication.instance()
    try:
        scheme = app.styleHints().colorScheme()  # Qt >= 6.5
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except (AttributeError, TypeError):
        pass
    return "light"


def stylesheet(c: Dict[str, str]) -> str:
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif; font-size: 10pt; }}
QWidget {{ background: {c['window']}; color: {c['text']}; }}
QDialog, QMainWindow {{ background: {c['window']}; }}
QMenuBar, QMenu, QStatusBar {{ background: {c['card']}; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#preview {{ background: {c['preview']}; border: 1px dashed {c['border']}; border-radius: 12px; }}
QFrame#preview QLabel {{ background: transparent; color: {c['muted']}; }}
QLabel#title {{ font-size: 20pt; font-weight: 700; background: transparent; }}
QLabel#subtitle, QLabel#muted {{ color: {c['muted']}; background: transparent; }}
QLabel#statusTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#fieldName {{ color: {c['muted']}; }}
QLabel#warning {{ color: {c['warn']}; background: transparent; }}
QLabel#stepTitle {{ font-size: 14pt; font-weight: 600; background: transparent; }}
QPushButton {{ background: {c['button']}; color: {c['text']}; border: 1px solid {c['border']};
  border-radius: 8px; padding: 8px 16px; }}
QPushButton:hover {{ background: {c['button_hover']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none; font-weight: 600;
  padding: 10px 24px; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#danger {{ background: {c['err']}; color: #ffffff; border: none; font-weight: 600; padding: 10px 24px; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['card']}; top: -1px; }}
QTabBar::tab {{ background: {c['window']}; color: {c['muted']}; padding: 8px 16px; border: 1px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; background: {c['card']}; border: 1px solid {c['border']};
  border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabWidget QWidget {{ background: {c['card']}; }}
QComboBox, QSpinBox {{ background: {c['window']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; selection-background-color: {c['accent']};
  selection-color: {c['accent_text']}; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QSlider::groove:horizontal {{ height: 6px; background: {c['border']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 16px; margin: -5px 0; border-radius: 8px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
QProgressBar {{ background: {c['border']}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; }}
"""


def apply_theme(app: QApplication, setting: str) -> str:
    """Applique le thème et retourne le mode effectif (« light » ou « dark »)."""
    mode = resolve_mode(setting, app)
    c = PALETTES[mode]
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["window"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["card"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["button"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(c))
    return mode
