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

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()


class GLVideoWidget(_FramePainter, QOpenGLWidget):
    """Rendu OpenGL : l'image est envoyée à la carte graphique et mise à l'échelle par elle."""
    resized = Signal(int, int)
    double_clicked = Signal()
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

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()


def create_video_widget(parent=None, force_software: bool = False):
    if force_software or os.environ.get("ANDROMIRROR_NO_GL") == "1":
        return SoftwareVideoWidget(parent)
    try:
        return GLVideoWidget(parent)
    except Exception:  # noqa: BLE001
        log.exception("OpenGL indisponible : affichage logiciel")
        return SoftwareVideoWidget(parent)
