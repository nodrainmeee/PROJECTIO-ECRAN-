"""Fenêtre des réglages : qualité, audio, général. Tout est enregistré immédiatement."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                             QPushButton, QSlider, QTabWidget, QVBoxLayout, QWidget)

from ..settings import SettingsStore

RESOLUTION_LABELS = [("native", "Résolution native du téléphone (jusqu'à la 4K)"),
                     ("2160", "4K (2160p)"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p")]
FPS_LABELS = [(30, "30 images par seconde"), (60, "60 images par seconde")]
CODEC_LABELS = [("h264", "H.264 — compatibilité maximale"), ("h265", "H.265 — meilleure qualité à débit égal")]
THEME_LABELS = [("system", "Automatique (comme Windows)"), ("light", "Clair"), ("dark", "Sombre")]


def _combo(options, current) -> QComboBox:
    box = QComboBox()
    for value, label in options:
        box.addItem(label, value)
    idx = box.findData(current)
    box.setCurrentIndex(max(idx, 0))
    return box


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Réglages")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # -- Qualité --------------------------------------------------------------
        q = QWidget()
        form = QFormLayout(q)
        form.setContentsMargins(16, 16, 16, 16)
        form.setVerticalSpacing(12)
        self.resolution = _combo(RESOLUTION_LABELS, store.get("quality", "resolution"))
        self.fps = _combo(FPS_LABELS, store.get("quality", "fps"))
        self.codec = _combo(CODEC_LABELS, store.get("quality", "codec"))
        self.hw_decode = QCheckBox("Utiliser la carte graphique pour décoder la vidéo (si possible)")
        self.hw_decode.setChecked(store.get("quality", "hw_decode"))
        self.bitrate = QSlider(Qt.Orientation.Horizontal)
        self.bitrate.setRange(4, 60)
        self.bitrate.setValue(store.get("quality", "bitrate_mbps"))
        self.bitrate_label = QLabel()
        self._set_bitrate_label(self.bitrate.value())
        brow = QHBoxLayout()
        brow.addWidget(self.bitrate, 1)
        brow.addWidget(self.bitrate_label)
        form.addRow("Résolution :", self.resolution)
        form.addRow("Débit vidéo :", brow)
        form.addRow("Fréquence d'images :", self.fps)
        form.addRow("Codec vidéo :", self.codec)
        form.addRow("", self.hw_decode)
        note = QLabel("Ces réglages sont enregistrés tout de suite et appliqués à la prochaine connexion.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(q, "Qualité")

        # -- Audio ----------------------------------------------------------------
        a = QWidget()
        al = QVBoxLayout(a)
        al.setContentsMargins(16, 16, 16, 16)
        al.setSpacing(12)
        self.forward_audio = QCheckBox("Envoyer le son du téléphone vers l'ordinateur")
        self.forward_audio.setChecked(store.get("audio", "forward_audio"))
        self.mute_phone = QCheckBox("Le haut-parleur du téléphone reste muet pendant le transfert")
        self.mute_phone.setChecked(store.get("audio", "mute_phone"))
        self.mute_phone.setEnabled(False)       # imposé par la méthode de capture, pas un vrai choix
        self.mute_phone.setToolTip("Le son détourné vers l'ordinateur ne sort plus sur le téléphone : ce n'est "
                                   "pas réglable dans cette version.")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(store.get("audio", "volume"))
        self.volume_label = QLabel()
        self._set_volume_label(self.volume.value())
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("Volume :"))
        vrow.addWidget(self.volume, 1)
        vrow.addWidget(self.volume_label)
        al.addWidget(self.forward_audio)
        al.addWidget(self.mute_phone)
        al.addLayout(vrow)
        note2 = QLabel("Le transfert audio demande Android 11 ou plus récent. Le son du téléphone est détourné "
                       "vers l'ordinateur : c'est ce qui rend le téléphone muet. Le volume se règle pendant l'affichage.")
        note2.setObjectName("muted")
        note2.setWordWrap(True)
        al.addWidget(note2)
        al.addStretch(1)
        tabs.addTab(a, "Audio")

        # -- Général --------------------------------------------------------------
        g = QWidget()
        gf = QFormLayout(g)
        gf.setContentsMargins(16, 16, 16, 16)
        gf.setVerticalSpacing(12)
        self.theme = _combo(THEME_LABELS, store.get("general", "theme"))
        self.auto_reconnect = QCheckBox("Reconnecter automatiquement après un débranchement")
        self.auto_reconnect.setChecked(store.get("general", "auto_reconnect"))
        self.keep_screen_off = QCheckBox("Garder l'écran du téléphone éteint pendant l'utilisation")
        self.keep_screen_off.setChecked(store.get("general", "keep_screen_off"))
        self.show_wizard = QCheckBox("Réafficher l'assistant de première utilisation au prochain démarrage")
        self.show_wizard.setChecked(not store.get("general", "wizard_done"))
        gf.addRow("Thème :", self.theme)
        gf.addRow(self.auto_reconnect)
        gf.addRow(self.keep_screen_off)
        gf.addRow(self.show_wizard)
        tabs.addTab(g, "Général")

        self.saved_label = QLabel("Les réglages sont enregistrés automatiquement.")
        self.saved_label.setObjectName("muted")
        root.addWidget(self.saved_label)
        row = QHBoxLayout()
        self.btn_reset = QPushButton("Valeurs par défaut")
        self.btn_close = QPushButton("Fermer")
        self.btn_close.setObjectName("primary")
        row.addWidget(self.btn_reset)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        root.addLayout(row)

        # -- câblage : chaque changement est enregistré tout de suite ---------------
        self.resolution.currentIndexChanged.connect(lambda _: self._save("quality", "resolution", self.resolution.currentData()))
        self.fps.currentIndexChanged.connect(lambda _: self._save("quality", "fps", self.fps.currentData()))
        self.codec.currentIndexChanged.connect(lambda _: self._save("quality", "codec", self.codec.currentData()))
        self.hw_decode.toggled.connect(lambda v: self._save("quality", "hw_decode", v))
        self.bitrate.valueChanged.connect(self._on_bitrate)
        self.forward_audio.toggled.connect(lambda v: self._save("audio", "forward_audio", v))
        self.mute_phone.toggled.connect(lambda v: self._save("audio", "mute_phone", v))
        self.volume.valueChanged.connect(self._on_volume)
        self.theme.currentIndexChanged.connect(lambda _: self._save("general", "theme", self.theme.currentData()))
        self.auto_reconnect.toggled.connect(lambda v: self._save("general", "auto_reconnect", v))
        self.keep_screen_off.toggled.connect(lambda v: self._save("general", "keep_screen_off", v))
        self.show_wizard.toggled.connect(lambda v: self._save("general", "wizard_done", not v))
        self.btn_reset.clicked.connect(self._reset)
        self.btn_close.clicked.connect(self.accept)

    # -- helpers ---------------------------------------------------------------------------
    def _set_bitrate_label(self, v: int) -> None:
        self.bitrate_label.setText(f"{v} Mb/s")

    def _set_volume_label(self, v: int) -> None:
        self.volume_label.setText(f"{v} %")

    def _on_bitrate(self, v: int) -> None:
        self._set_bitrate_label(v)
        self._save("quality", "bitrate_mbps", v)

    def _on_volume(self, v: int) -> None:
        self._set_volume_label(v)
        self._save("audio", "volume", v)

    def _save(self, section: str, key: str, value) -> None:
        try:
            self.store.set(section, key, value)
        except ValueError:
            return
        if self.store.last_save_ok:
            self.saved_label.setText("✔ Enregistré.")
        else:
            self.saved_label.setText("⚠ Enregistrement impossible (voir le journal d'erreurs).")

    def _reset(self) -> None:
        keep_wizard = self.store.get("general", "wizard_done")
        self.store.reset_section("quality", "audio", "general")
        self.store.set("general", "wizard_done", keep_wizard)
        self._load_from_store()
        self.saved_label.setText("✔ Valeurs par défaut rétablies.")

    def _load_from_store(self) -> None:
        s = self.store
        widgets = [self.resolution, self.fps, self.codec, self.hw_decode, self.bitrate, self.forward_audio,
                   self.mute_phone, self.volume, self.theme, self.auto_reconnect,
                   self.keep_screen_off, self.show_wizard]
        for w in widgets:
            w.blockSignals(True)
        self.resolution.setCurrentIndex(max(self.resolution.findData(s.get("quality", "resolution")), 0))
        self.fps.setCurrentIndex(max(self.fps.findData(s.get("quality", "fps")), 0))
        self.codec.setCurrentIndex(max(self.codec.findData(s.get("quality", "codec")), 0))
        self.hw_decode.setChecked(s.get("quality", "hw_decode"))
        self.bitrate.setValue(s.get("quality", "bitrate_mbps"))
        self._set_bitrate_label(self.bitrate.value())
        self.forward_audio.setChecked(s.get("audio", "forward_audio"))
        self.mute_phone.setChecked(s.get("audio", "mute_phone"))
        self.volume.setValue(s.get("audio", "volume"))
        self._set_volume_label(self.volume.value())
        self.theme.setCurrentIndex(max(self.theme.findData(s.get("general", "theme")), 0))
        self.auto_reconnect.setChecked(s.get("general", "auto_reconnect"))
        self.keep_screen_off.setChecked(s.get("general", "keep_screen_off"))
        self.show_wizard.setChecked(not s.get("general", "wizard_done"))
        for w in widgets:
            w.blockSignals(False)
