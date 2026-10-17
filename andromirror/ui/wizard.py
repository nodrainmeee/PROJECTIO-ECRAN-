"""Assistant de première utilisation en 3 étapes (F2)."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
                             QVBoxLayout, QWidget)

from ..connection.models import ConnectionSnapshot, ConnectionState

STEPS = [
    ("Étape 1 sur 3 — Activer les options pour les développeurs",
     "<p>Ces options sont cachées par défaut sur Android. Pour les faire apparaître :</p>"
     "<ol>"
     "<li>Ouvrez <b>Paramètres</b> sur le téléphone.</li>"
     "<li>Allez dans <b>À propos du téléphone</b> (parfois <i>Informations sur le logiciel</i>).</li>"
     "<li>Touchez <b>7 fois de suite</b> sur <b>Numéro de build</b> (sur certaines marques : "
     "<i>Version de MIUI</i>, <i>Version de HyperOS</i> ou <i>Version de build</i>).</li>"
     "<li>Entrez le code ou le schéma de verrouillage si le téléphone le demande.</li>"
     "<li>Un message indique « Vous êtes maintenant développeur ».</li>"
     "</ol>"
     "<p class='n'>Les noms des menus varient selon la marque (Samsung, Xiaomi, Tecno, Infinix…).</p>"),
    ("Étape 2 sur 3 — Activer le débogage USB",
     "<p>Le débogage USB permet à l'ordinateur de dialoguer avec le téléphone.</p>"
     "<ol>"
     "<li>Retournez dans <b>Paramètres</b>.</li>"
     "<li>Ouvrez <b>Options pour les développeurs</b> (souvent dans <b>Système</b>, ou tout en bas "
     "des paramètres).</li>"
     "<li>Activez l'interrupteur en haut de la page si nécessaire.</li>"
     "<li>Activez <b>Débogage USB</b> et confirmez avec <b>OK</b>.</li>"
     "</ol>"
     "<p class='n'>Sur certains téléphones (Xiaomi par exemple), il faut aussi activer "
     "« Débogage USB (paramètres de sécurité) » pour le contrôle à la souris.</p>"),
    ("Étape 3 sur 3 — Brancher le téléphone et accepter",
     "<ol>"
     "<li>Branchez le téléphone à l'ordinateur avec un <b>bon câble USB</b> (de préférence celui "
     "d'origine).</li>"
     "<li>Si le téléphone propose un mode USB, choisissez <b>Transfert de fichiers</b>.</li>"
     "<li>Sur le téléphone, une fenêtre <b>« Autoriser le débogage USB ? »</b> apparaît : "
     "cochez <b>Toujours autoriser depuis cet ordinateur</b>, puis touchez <b>Autoriser</b>.</li>"
     "</ol>"),
]


class WizardDialog(QDialog):
    finished_ok = Signal()   # l'utilisateur a terminé l'assistant

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistant de première utilisation")
        self.setMinimumSize(560, 480)
        self._index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        self._progress_label = QLabel()
        self._progress_label.setObjectName("muted")
        root.addWidget(self._progress_label)

        self._stack = QStackedWidget()
        for title, html in STEPS:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            t = QLabel(title)
            t.setObjectName("stepTitle")
            t.setWordWrap(True)
            body = QLabel(f"<style>p.n {{ color: gray; }} li {{ margin-bottom: 6px; }}</style>{html}")
            body.setTextFormat(Qt.TextFormat.RichText)
            body.setWordWrap(True)
            body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            lay.addWidget(t)
            lay.addWidget(body, 1)
            if title.startswith("Étape 3"):
                self._live = QLabel("En attente du téléphone…")
                self._live.setObjectName("card")
                self._live.setWordWrap(True)
                self._live.setMargin(10)
                self._live.setStyleSheet("QLabel#card { border: 1px solid #888; border-radius: 8px; }")
                lay.addWidget(self._live)
            self._stack.addWidget(page)
        root.addWidget(self._stack, 1)

        row = QHBoxLayout()
        self.btn_prev = QPushButton("Précédent")
        self.btn_next = QPushButton("Suivant")
        self.btn_next.setObjectName("primary")
        self.btn_cancel = QPushButton("Fermer")
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        row.addWidget(self.btn_prev)
        row.addWidget(self.btn_next)
        root.addLayout(row)

        self.btn_prev.clicked.connect(self.previous)
        self.btn_next.clicked.connect(self.next)
        self.btn_cancel.clicked.connect(self.reject)
        self._update()

    # -- navigation --------------------------------------------------------------------
    @property
    def step(self) -> int:
        return self._index

    def next(self) -> None:
        if self._index < len(STEPS) - 1:
            self._index += 1
            self._update()
        else:
            self.finished_ok.emit()
            self.accept()

    def previous(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._update()

    def _update(self) -> None:
        self._stack.setCurrentIndex(self._index)
        self._progress_label.setText(f"{self._index + 1} / {len(STEPS)}")
        self.btn_prev.setEnabled(self._index > 0)
        last = self._index == len(STEPS) - 1
        self.btn_next.setText("Terminer" if last else "Suivant")

    # -- état en direct (étape 3) ---------------------------------------------------------
    def on_snapshot(self, snap: ConnectionSnapshot) -> None:
        if snap.state is ConnectionState.UNAUTHORIZED:
            text = "⏳ Téléphone détecté — touchez « Autoriser » sur son écran."
        elif snap.device is not None:
            text = f"✅ Téléphone détecté : {snap.device.display_name}. Vous pouvez terminer l'assistant."
        elif snap.state is ConnectionState.ERROR:
            text = f"⚠ {snap.message}"
        else:
            text = "En attente du téléphone… (branchez le câble USB)"
        self._live.setText(text)

    def live_text(self) -> str:
        return self._live.text()
