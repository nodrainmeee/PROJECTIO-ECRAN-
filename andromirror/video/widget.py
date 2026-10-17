"""Zone d'affichage de l'écran du téléphone : QOpenGLWidget (carte graphique) avec repli sur un widget classique.

Le rendu garde toujours le rapport largeur/hauteur du téléphone (bandes noires si besoin). Le décodeur envoie
des images déjà réduites à la taille de la zone ; elles sont donc dessinées presque pixel pour pixel.
"""
from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from ..control.keymap import KEYCODE_BACK, KEYCODE_HOME
from ..control.mapping import widget_to_video
from ..logging_setup import get_logger
from .decoder import DecodedFrame

log = get_logger("affichage")


def fit_rect(widget_w: float, widget_h: float, src_w: float, src_h: float) -> QRectF:
    """Plus grand rectangle de rapport src_w:src_h contenu dans le widget, centré."""
    if src_w <= 0 or src_h <= 0 or widget_w <= 0 or widget_h <= 0:
        return QRectF(0, 0, 0, 0)
    scale = min(widget_w / src_w, widget_h / src_h)
    w, h = src_w * scale, src_h * scale
    return QRectF((widget_w - w) / 2.0, (widget_h - h) / 2.0, w, h)


class _FramePainter:
    """Partie commune : mémorise la dernière image et la dessine. (Mélangée à un widget Qt.)"""

    def _init_painter(self) -> None:
        self._frame: Optional[DecodedFrame] = None
        self._image: Optional[QImage] = None
        self._message = "En attente de l'image du téléphone…"
        self._target_pixels: Tuple[int, int] = (0, 0)
        self.frames_painted = 0
        self.last_rect = QRectF()
        # contrôle : activé par la fenêtre d'affichage quand un contrôleur est branché
        self.control_enabled = False
        self._left_down = False
        self._last_point: Optional[Tuple[int, int, int, int]] = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- API ------------------------------------------------------------------------------------------
    def set_frame(self, frame: DecodedFrame) -> None:
        # `frame` garde les octets en vie : QImage ne les copie pas.
        self._image = QImage(frame.data, frame.width, frame.height, frame.stride, QImage.Format.Format_RGB888)
        self._frame = frame
        self._message = ""
        self.update()

    def clear(self, message: str = "") -> None:
        self._frame = None
        self._image = None
        self._message = message
        self.update()

    def set_message(self, message: str) -> None:
        """Texte affiché par-dessus l'image (ou à la place d'elle). Vide : aucun."""
        self._message = message
        self.update()

    @property
    def message(self) -> str:
        return self._message

    @property
    def has_frame(self) -> bool:
        return self._image is not None

    @property
    def target_pixels(self) -> Tuple[int, int]:
        """Taille de la zone en pixels réels (lisible depuis n'importe quel thread)."""
        return self._target_pixels

    def _update_target(self) -> None:
        dpr = self.devicePixelRatioF()
        self._target_pixels = (int(self.width() * dpr), int(self.height() * dpr))


    # -- contrôle : souris et clavier -> signaux (coordonnées en pixels de la VIDÉO du téléphone) ------------------
    def _map_point(self, pos, clamp: bool) -> Optional[Tuple[int, int, int, int]]:
        """Point de la fenêtre -> (x, y, largeur vidéo, hauteur vidéo), ou None hors de l'image."""
        f = self._frame
        if f is None:
            return None
        r = fit_rect(self.width(), self.height(), f.native_width, f.native_height)
        pt = widget_to_video(pos.x(), pos.y(), (r.x(), r.y(), r.width(), r.height()),
                             f.native_width, f.native_height, clamp)
        if pt is None:
            return None
        return pt[0], pt[1], f.native_width, f.native_height

    def _control_press(self, event) -> None:
        self.setFocus()
        button = event.button()
        pt = self._map_point(event.position(), clamp=False)
        if pt is None:
            return
        if button == Qt.MouseButton.LeftButton:
            self._left_down = True
            self._last_point = pt
            self.touch_event.emit(0, *pt)
        elif button == Qt.MouseButton.RightButton:
            self.side_button.emit(KEYCODE_BACK)         # clic droit = Retour
        elif button == Qt.MouseButton.MiddleButton:
            self.side_button.emit(KEYCODE_HOME)         # clic molette = Accueil

    def mousePressEvent(self, event) -> None:
        if self.control_enabled:
            self._control_press(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.control_enabled and self._left_down:
            pt = self._map_point(event.position(), clamp=True)
            if pt is not None:
                self._last_point = pt
                self.touch_event.emit(2, *pt)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.control_enabled and event.button() == Qt.MouseButton.LeftButton and self._left_down:
            self._left_down = False
            pt = self._map_point(event.position(), clamp=True) or self._last_point
            if pt is not None:
                self.touch_event.emit(1, *pt)
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if not self.control_enabled:
            super().wheelEvent(event)
            return
        pt = self._map_point(event.position(), clamp=False)
        if pt is not None:
            notches = event.angleDelta().y() / 120.0
            if notches:
                self.wheel_event.emit(*pt, notches)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if not self.control_enabled:
            super().keyPressEvent(event)
            return
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        self.key_down.emit(int(event.key()), event.text(), ctrl, alt)
        event.accept()

    def keyReleaseEvent(self, event) -> None:
        if not self.control_enabled:
            super().keyReleaseEvent(event)
            return
        if not event.isAutoRepeat():
            self.key_up.emit(int(event.key()))
        event.accept()

    def focusNextPrevChild(self, next_child: bool) -> bool:
        if self.control_enabled:
            return False                 # Tab est envoyé au téléphone, il ne change pas le focus du PC
        return super().focusNextPrevChild(next_child)

    def focusOutEvent(self, event) -> None:
        if self._left_down:              # fenêtre quittée en plein glissé : on relâche le doigt
            self._left_down = False
            if self._last_point is not None:
                self.touch_event.emit(1, *self._last_point)
        self.focus_lost.emit()
        super().focusOutEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.control_enabled:
            self._control_press(event)   # le 2e appui d'un double-clic arrive ici : c'est un vrai appui sur l'écran
        else:
            self.double_clicked.emit()

    # -- dessin ----------------------------------------------------------------------------------------
    def _paint(self, p: QPainter) -> None:
        p.fillRect(self.rect(), QColor(0, 0, 0))
        img = self._image
        if img is not None and self._frame is not None:
            f = self._frame
            rect = fit_rect(self.width(), self.height(), f.native_width, f.native_height)
            self.last_rect = rect
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(rect, img)
            self.frames_painted += 1
        if self._message:
            font = QFont(p.font())
            font.setPointSize(max(font.pointSize(), 11))
            p.setFont(font)
            metrics = p.fontMetrics()
            flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
            box = metrics.boundingRect(0, 0, max(200, int(self.width() * 0.8)), self.height(), int(flags),
                                       self._message)
            box.adjust(-18, -12, 18, 12)
            box.moveCenter(self.rect().center())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 20, 24, 215))
            p.drawRoundedRect(box, 10, 10)
            p.setPen(QColor(235, 235, 240))
            p.drawText(box, int(flags), self._message)


class SoftwareVideoWidget(_FramePainter, QWidget):
    """Version sans OpenGL (repli automatique si la carte graphique ne fournit pas OpenGL)."""
    resized = Signal(int, int)
    double_clicked = Signal()
    touch_event = Signal(int, int, int, int, int)        # action (0 appui, 1 relâché, 2 déplacé), x, y, larg., haut. vidéo
    wheel_event = Signal(int, int, int, int, float)      # x, y, larg., haut. vidéo, crans (positif = vers le haut)
    side_button = Signal(int)                            # code Android (clic droit : Retour, clic molette : Accueil)
    key_down = Signal(int, str, bool, bool)              # code Qt, texte, Ctrl, Alt
    key_up = Signal(int)
    focus_lost = Signal()
    kind = "logiciel"

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self._init_painter()
        self.setMinimumSize(160, 120)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        self._paint(p)
        p.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_target()
        self.resized.emit(*self._target_pixels)


class GLVideoWidget(_FramePainter, QOpenGLWidget):
    """Rendu OpenGL : l'image est envoyée à la carte graphique et mise à l'échelle par elle."""
    resized = Signal(int, int)
    double_clicked = Signal()
    touch_event = Signal(int, int, int, int, int)        # action (0 appui, 1 relâché, 2 déplacé), x, y, larg., haut. vidéo
    wheel_event = Signal(int, int, int, int, float)      # x, y, larg., haut. vidéo, crans (positif = vers le haut)
    side_button = Signal(int)                            # code Android (clic droit : Retour, clic molette : Accueil)
    key_down = Signal(int, str, bool, bool)              # code Qt, texte, Ctrl, Alt
    key_up = Signal(int)
    focus_lost = Signal()
    kind = "OpenGL"

    def __init__(self, parent=None):
        QOpenGLWidget.__init__(self, parent)
        self._init_painter()
        self.setMinimumSize(160, 120)

    def paintGL(self) -> None:
        p = QPainter(self)
        self._paint(p)
        p.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_target()
        self.resized.emit(*self._target_pixels)


def create_video_widget(parent=None, force_software: bool = False):
    if force_software or os.environ.get("ANDROMIRROR_NO_GL") == "1":
        return SoftwareVideoWidget(parent)
    try:
        return GLVideoWidget(parent)
    except Exception:  # noqa: BLE001
        log.exception("OpenGL indisponible : affichage logiciel")
        return SoftwareVideoWidget(parent)
