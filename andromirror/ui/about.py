"""Boîte « À propos » avec mention de licence d'ADB."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from .. import APP_NAME, __version__

ABOUT_HTML = f"""
<h3>{APP_NAME} {__version__}</h3>
<p>Miroir et contrôle d'un téléphone Android depuis un PC Windows.<br>
<i>Étape 1 : connexion et détection du téléphone. L'affichage de l'écran, le contrôle et le son
arrivent dans les prochaines étapes.</i></p>
<p><b>Composants tiers</b><br>
• <b>ADB</b> (Android Debug Bridge, « platform-tools »), © The Android Open Source Project,
distribué sous <b>licence Apache 2.0</b> —
<a href="https://www.apache.org/licenses/LICENSE-2.0">apache.org/licenses/LICENSE-2.0</a>.
Fourni tel quel, sans modification.<br>
• <b>PySide6 / Qt</b> (The Qt Company) — licence <b>LGPL v3</b>, liaison dynamique : les bibliothèques Qt restent des fichiers séparés et remplaçables. Détails dans <i>AVIS-LICENCES.txt</i>.<br>
• <b>FFmpeg / PyAV</b> : prévus pour une étape ultérieure (non inclus pour l'instant).</p>
"""


def show_about(parent: QWidget | None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(f"À propos d'{APP_NAME}")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(ABOUT_HTML)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
