"""Fenêtre principale : état de la connexion, informations du téléphone, accès aux réglages."""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                             QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, paths
from ..connection.models import ConnectionSnapshot, ConnectionState
from ..connection.monitor import ConnectionController
from ..logging_setup import get_logger
from ..settings import SettingsStore
from . import theme
from .about import show_about
from .display_manager import DisplayManager
from .settings_dialog import SettingsDialog
from .wizard import WizardDialog

log = get_logger("ui")

MIN_STABLE_SECONDS = 5.0     # en dessous, une coupure est considérée comme un échec de démarrage
PREVIEW_IDLE = ("L'écran du téléphone s'affiche dans une fenêtre séparée dès que vous cliquez sur « Connecter ».\n"
                "Plein écran : touche F11 (Échap pour en sortir).")


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


class MainWindow(QMainWindow):
    fatal_requested = Signal(str)   # peut être émis depuis n'importe quel thread

    def __init__(self, store: SettingsStore, controller: ConnectionController, auto_wizard: bool = True,
                 display: Optional[DisplayManager] = None):
        super().__init__()
        self.store = store
        self.controller = controller
        self._mode = "light"
        self._snap = ConnectionSnapshot()
        self._wizard: Optional[WizardDialog] = None
        self._fatal_shown = False
        self._display_wanted = False      # l'utilisateur a cliqué sur « Connecter »
        self._display_started_at = 0.0
        self.display = display or DisplayManager(store, controller.adb, parent=self)

        self.setWindowTitle(APP_NAME)
        icon = paths.resource_path("assets/icon.png")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(store.get("window", "width"), store.get("window", "height"))
        self.setMinimumSize(560, 520)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 12)
        root.setSpacing(14)

        # -- en-tête
        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        sub = QLabel("Affichez et contrôlez votre téléphone Android depuis votre PC")
        sub.setObjectName("subtitle")
        head.addWidget(title)
        head.addWidget(sub)
        root.addLayout(head)

        # -- zone d'état
        self.status_card = _card()
        sc = QHBoxLayout(self.status_card)
        sc.setContentsMargins(18, 16, 18, 16)
        sc.setSpacing(14)
        self.dot = QLabel()
        self.dot.setFixedSize(16, 16)
        sc.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        self.status_title = QLabel("Non connecté")
        self.status_title.setObjectName("statusTitle")
        self.status_message = QLabel()
        self.status_message.setWordWrap(True)
        self.status_message.setObjectName("muted")
        text_col.addWidget(self.status_title)
        text_col.addWidget(self.status_message)
        sc.addLayout(text_col, 1)
        root.addWidget(self.status_card)

        # -- infos du téléphone
        info = _card()
        grid = QGridLayout(info)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        self._fields = {}
        for row, (key, label) in enumerate([("model", "Modèle"), ("android", "Version d'Android"),
                                            ("screen", "Écran"), ("serial", "Numéro de série")]):
            name = QLabel(label)
            name.setObjectName("fieldName")
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._fields[key] = value
        grid.setColumnStretch(1, 1)
        self.warning = QLabel()
        self.warning.setObjectName("warning")
        self.warning.setWordWrap(True)
        self.warning.hide()
        grid.addWidget(self.warning, len(self._fields), 0, 1, 2)
        root.addWidget(info)

        # -- aperçu (réservé aux étapes suivantes)
        preview = QFrame()
        preview.setObjectName("preview")
        pl = QVBoxLayout(preview)
        self.preview_msg = QLabel(PREVIEW_IDLE)
        self.preview_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_msg.setWordWrap(True)
        pl.addWidget(self.preview_msg)
        root.addWidget(preview, 1)

        # -- boutons
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_connect = QPushButton("Connecter")
        self.btn_connect.setObjectName("primary")
        self.btn_wizard = QPushButton("Assistant de première utilisation")
        self.btn_settings = QPushButton("Réglages")
        self.btn_theme = QPushButton()
        row.addWidget(self.btn_connect)
        row.addStretch(1)
        row.addWidget(self.btn_wizard)
        row.addWidget(self.btn_settings)
        row.addWidget(self.btn_theme)
        root.addLayout(row)

        # -- menu et barre d'état
        m_file = self.menuBar().addMenu("&Fichier")
        act_quit = QAction("&Quitter", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)
        m_help = self.menuBar().addMenu("&Aide")
        for label, slot in [("Assistant de première utilisation", self.open_wizard),
                            ("Ouvrir le dossier du journal d'erreurs", self.open_log_folder),
                            ("À propos", lambda: show_about(self))]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            m_help.addAction(a)
        self.adb_label = QLabel("ADB : —")
        self.statusBar().addPermanentWidget(self.adb_label)
        self.statusBar().showMessage(f"{APP_NAME} {__version__}")

        # -- câblage
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.btn_wizard.clicked.connect(self.open_wizard)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_theme.clicked.connect(self.toggle_theme)
        controller.snapshot_changed.connect(self.on_snapshot)
        controller.device_lost.connect(self._on_lost)
        controller.device_reconnected.connect(self._on_reconnected)
        self.display.note.connect(self._on_display_note)
        self.display.finished.connect(self._on_display_finished)
        self.display.user_closed.connect(self._on_display_user_closed)
        store.subscribe(self._on_setting)
        self.fatal_requested.connect(self.show_fatal)

        self.apply_theme()
        self.on_snapshot(controller.last_snapshot)
        self._auto_wizard = auto_wizard

    # -- cycle de vie -----------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._auto_wizard and not self.store.get("general", "wizard_done"):
            self._auto_wizard = False
            QTimer.singleShot(0, self.open_wizard)

    def closeEvent(self, event) -> None:
        self.store.set("window", "width", self.width())
        self.store.set("window", "height", self.height())
        self.store.unsubscribe(self._on_setting)
        self._display_wanted = False      # fermeture : aucune reprise automatique de l'affichage
        self.display.shutdown()
        self.controller.shutdown()
        super().closeEvent(event)

    # -- actions ------------------------------------------------------------------------------
    def toggle_connection(self) -> None:
        if self._snap.session_active:
            self._display_wanted = False
            self.display.stop()
            self.controller.disconnect_device()
        else:
            self._display_wanted = True
            self.preview_msg.setText(PREVIEW_IDLE)
            self.controller.connect_device()
        self.btn_connect.setEnabled(False)   # réactivé au prochain état reçu

    def open_settings(self) -> None:
        SettingsDialog(self.store, self).exec()

    def open_wizard(self) -> None:
        wiz = WizardDialog(self)
        self._wizard = wiz
        wiz.on_snapshot(self._snap)
        self.controller.snapshot_changed.connect(wiz.on_snapshot)
        wiz.finished_ok.connect(lambda: self.store.set("general", "wizard_done", True))
        wiz.exec()
        try:
            self.controller.snapshot_changed.disconnect(wiz.on_snapshot)
        except TypeError:
            pass
        self._wizard = None
        # Fermer l'assistant sans le terminer ne le marque pas comme fait, mais on ne le réimpose
        # pas pendant cette session.

    def open_log_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir())))

    def toggle_theme(self) -> None:
        self.store.set("general", "theme", "light" if self._mode == "dark" else "dark")

    def apply_theme(self) -> None:
        self._mode = theme.apply_theme(QApplication.instance(), self.store.get("general", "theme"))
        self.btn_theme.setText("Thème clair" if self._mode == "dark" else "Thème sombre")
        self._paint_status()

    def _on_setting(self, section: str, key: str, value) -> None:
        if (section, key) == ("general", "theme"):
            self.apply_theme()
        elif (section, key) == ("general", "auto_reconnect"):
            self.controller.set_auto_reconnect(bool(value))

    # -- état de la connexion ---------------------------------------------------------------------
    def on_snapshot(self, snap: ConnectionSnapshot) -> None:
        self._snap = snap
        self.status_title.setText(snap.title)
        self.status_message.setText(snap.message)
        self._paint_status()

        dev = snap.device
        self._fields["model"].setText(dev.display_name if dev else "—")
        self._fields["android"].setText(dev.android_text if dev else "—")
        self._fields["screen"].setText(dev.screen_text if dev else "—")
        self._fields["serial"].setText(dev.serial if dev else "—")

        notes = []
        if dev and not dev.android_supported:
            notes.append("Ce téléphone utilise une version d'Android trop ancienne (Android 8 minimum).")
        if dev and not dev.audio_supported:
            notes.append("Le transfert du son demande Android 11 ou plus récent.")
        self.warning.setText(" ".join(notes))
        self.warning.setVisible(bool(notes))

        self.btn_connect.setEnabled(True)
        if snap.session_active:
            self.btn_connect.setText("Déconnecter")
            self.btn_connect.setObjectName("danger")
        else:
            self.btn_connect.setText("Connecter")
            self.btn_connect.setObjectName("primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)

        self.adb_label.setText(f"ADB {snap.adb_version}" if snap.adb_version else
                               ("ADB : indisponible" if not snap.adb_ok else "ADB : —"))
        self._maybe_start_display(snap)

    def _paint_status(self) -> None:
        c = theme.PALETTES[self._mode]
        color = {
            ConnectionState.CONNECTED: c["ok"],
            ConnectionState.UNAUTHORIZED: c["warn"],
            ConnectionState.ERROR: c["err"],
            ConnectionState.DISCONNECTED: c["idle"],
        }[self._snap.state]
        self.dot.setStyleSheet(f"background: {color}; border-radius: 8px;")

    def _on_lost(self, serial: str) -> None:
        self.statusBar().showMessage("Téléphone débranché.", 6000)
        # l'affichage est fermé proprement ; il reprendra tout seul au rebranchement (reconnexion automatique)
        self.display.stop("Téléphone débranché : l'affichage a été fermé. Rebranchez-le pour reprendre.")

    # -- affichage de l'écran du téléphone ------------------------------------------------------------------
    def _maybe_start_display(self, snap: ConnectionSnapshot) -> None:
        if not snap.session_active:
            self._display_wanted = False
            return
        if not self._display_wanted or self.display.active:
            return
        if snap.state is not ConnectionState.CONNECTED or snap.device is None:
            return
        if not snap.device.android_supported:
            self.preview_msg.setText("Ce téléphone utilise une version d'Android trop ancienne (Android 8 minimum) : "
                                     "l'affichage n'est pas possible.")
            self._display_wanted = False
            return
        self._display_started_at = time.monotonic()
        self.display.start(snap.device)

    def _on_display_note(self, text: str) -> None:
        self.preview_msg.setText(text or PREVIEW_IDLE)

    def _on_display_finished(self, info) -> None:
        if info.reason == "stopped":
            self._maybe_start_display(self._snap)       # ex. : « Connecter » cliqué pendant un arrêt
            return
        lasted = time.monotonic() - self._display_started_at
        resume = (info.reason == "connection_lost" and lasted >= MIN_STABLE_SECONDS
                  and self.store.get("general", "auto_reconnect"))
        if resume:
            # coupure après un bon moment d'affichage (câble débranché...) : reprise automatique au retour
            self._maybe_start_display(self._snap)
            return
        # échec ou coupure immédiate : pas d'insistance en boucle
        self._display_wanted = False
        if info.reason != "connection_lost":
            self.preview_msg.setText(self.preview_msg.text()
                                     + "\n\nCliquez sur « Déconnecter » puis « Connecter » pour réessayer.")

    def _on_display_user_closed(self) -> None:
        # fermer la fenêtre d'affichage équivaut à « Déconnecter »
        self._display_wanted = False
        if self._snap.session_active:
            self.controller.disconnect_device()

    def _on_reconnected(self, serial: str) -> None:
        self.statusBar().showMessage("Téléphone reconnecté.", 6000)

    # -- erreurs inattendues ----------------------------------------------------------------------
    def show_fatal(self, text: str) -> None:
        """Affiche (une seule fois) un message d'erreur inattendue ; le détail est dans le journal."""
        if self._fatal_shown:
            return
        self._fatal_shown = True
        QMessageBox.critical(self, APP_NAME,
                             "Une erreur inattendue s'est produite.\n"
                             f"Détail : {text}\n\nElle a été notée dans le journal d'erreurs "
                             "(menu Aide → Ouvrir le dossier du journal d'erreurs).")
        self._fatal_shown = False
