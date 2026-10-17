"""Fenêtre d'affichage de l'écran du téléphone : image, barre d'outils, plein écran (F11 / Échap)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar

from .. import APP_NAME, paths
from ..video.widget import GLVideoWidget, SoftwareVideoWidget, create_video_widget

MIN_VIDEO = (240, 240)
SCREEN_FRACTION = 0.92


class DisplayWindow(QMainWindow):
    closed_by_user = Signal()
    target_changed = Signal(int, int)       # taille de la zone d'image en pixels réels

    def __init__(self, device_name: str = "", parent=None, force_software: bool = False):
        super().__init__(parent)
        self._device = device_name
        self._programmatic_close = False
        self._orientation: Optional[str] = None
        self._was_maximized = False
        self.setWindowTitle(f"{APP_NAME} — {device_name}" if device_name else APP_NAME)
        icon = paths.resource_path("assets/icon.png")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumSize(340, 300)
        self.resize(460, 820)

        self.video = create_video_widget(self, force_software)
        self.video.set_message("Connexion au téléphone…")
        self.setCentralWidget(self.video)
        self._wire_video()

        # -- barre d'outils : plein écran + emplacements réservés au contrôle (étape suivante)
        self.toolbar = QToolBar("Outils", self)
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.act_fullscreen = QAction("Plein écran", self)
        self.act_fullscreen.setToolTip("Plein écran (F11). Échap pour en sortir.")
        self.act_fullscreen.triggered.connect(self.toggle_fullscreen)
        self.toolbar.addAction(self.act_fullscreen)
        self.toolbar.addSeparator()
        self.placeholders = []
        # emplacements réservés (désactivés) : libellés courts pour tenir dans une fenêtre étroite
        for label, tip in (("◀", "Retour"), ("●", "Accueil"), ("■", "Applications récentes"),
                           ("Vol −", "Volume moins"), ("Vol +", "Volume plus"), ("⟲", "Rotation")):
            act = QAction(label, self)
            act.setEnabled(False)
            act.setToolTip(f"{tip} — le contrôle depuis le PC arrive à l'étape suivante.")
            self.toolbar.addAction(act)
            self.placeholders.append(act)

        self.info_label = QLabel("")
        self.statusBar().addWidget(self.info_label, 1)

        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.exit_fullscreen)

    # -- zone d'image ---------------------------------------------------------------------------------------
    def _wire_video(self) -> None:
        self.video.resized.connect(self.target_changed)
        self.video.double_clicked.connect(self.toggle_fullscreen)

    def ensure_valid_gl(self) -> bool:
        """Si OpenGL n'a pas pu démarrer sur ce PC, passe au rendu logiciel. Retourne True si remplacé."""
        v = self.video
        if isinstance(v, GLVideoWidget) and not v.isValid():
            message, frame = v.message, v._frame
            new = SoftwareVideoWidget(self)
            self.setCentralWidget(new)       # détruit l'ancien widget
            self.video = new
            if frame is not None:            # la dernière image reste affichée (le remplacement peut avoir lieu en cours de flux)
                new.set_frame(frame)
            new.set_message(message)
            self._wire_video()
            self.target_changed.emit(*new.target_pixels)
            return True
        return False

    def show_message(self, text: str) -> None:
        self.video.set_message(text)

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)

    # -- plein écran ---------------------------------------------------------------------------------------------
    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self) -> None:
        if self.isFullScreen():
            return
        self._was_maximized = self.isMaximized()
        self.toolbar.hide()
        self.statusBar().hide()
        self.showFullScreen()

    def exit_fullscreen(self) -> None:
        """Échap : ne fait rien hors plein écran (ne ferme jamais la fenêtre)."""
        if not self.isFullScreen():
            return
        self.toolbar.show()
        self.statusBar().show()
        if self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()

    # -- taille / orientation (F7) ------------------------------------------------------------------------
    @property
    def orientation(self) -> Optional[str]:
        return self._orientation

    def fit_to_video(self, width: int, height: int, force: bool = False) -> None:
        """Adapte la fenêtre au format de la vidéo quand l'orientation change (portrait <-> paysage)."""
        orientation = "landscape" if width >= height else "portrait"
        changed = orientation != self._orientation
        self._orientation = orientation
        if self.isFullScreen() or self.isMaximized() or not (changed or force):
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        dpr = screen.devicePixelRatio() or 1.0
        chrome_w = max(0, self.width() - self.video.width())
        chrome_h = max(0, self.height() - self.video.height())
        frame_w = max(0, self.frameGeometry().width() - self.width())
        frame_h = max(0, self.frameGeometry().height() - self.height())
        max_w = int(avail.width() * SCREEN_FRACTION) - chrome_w - frame_w
        max_h = int(avail.height() * SCREEN_FRACTION) - chrome_h - frame_h
        native_w, native_h = width / dpr, height / dpr          # taille « 1 pixel vidéo = 1 pixel écran »
        scale = min(max_w / native_w, max_h / native_h, 1.0)
        vw = max(MIN_VIDEO[0], int(native_w * scale))
        vh = max(MIN_VIDEO[1], int(native_h * scale))
        # on garde exactement le rapport de la vidéo
        if vw / vh > width / height:
            vw = max(1, int(vh * width / height))
        else:
            vh = max(1, int(vw * height / width))
        self.resize(chrome_w + vw, chrome_h + vh)
        # reste entièrement visible
        geo = self.frameGeometry()
        x = min(max(geo.x(), avail.left()), max(avail.left(), avail.right() - geo.width()))
        y = min(max(geo.y(), avail.top()), max(avail.top(), avail.bottom() - geo.height()))
        if (x, y) != (geo.x(), geo.y()):
            self.move(QPoint(x, y))

    # -- fermeture ---------------------------------------------------------------------------------------------------
    def close_programmatically(self) -> None:
        self._programmatic_close = True
        self.close()

    def closeEvent(self, event) -> None:
        if not self._programmatic_close:
            self.closed_by_user.emit()
        super().closeEvent(event)
