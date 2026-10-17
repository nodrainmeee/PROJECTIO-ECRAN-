"""Zone d'affichage : rapport largeur/hauteur conservé, messages, repli sans OpenGL."""
import time

import pytest
from PySide6.QtGui import QColor

from andromirror.video.decoder import DecodedFrame
from andromirror.video.widget import GLVideoWidget, SoftwareVideoWidget, create_video_widget, fit_rect

pytestmark = pytest.mark.usefixtures("qapp")


def solid(w, h, rgb, native=None):
    data = bytes(rgb) * (w * h)
    nw, nh = native or (w, h)
    return DecodedFrame(w, h, w * 3, data, 0, nw, nh, time.monotonic(), time.monotonic())


def test_fit_rect_math():
    r = fit_rect(400, 400, 100, 200)               # portrait dans un carré : bandes à gauche et à droite
    assert (r.width(), r.height(), r.x(), r.y()) == (200, 400, 100, 0)
    r = fit_rect(1000, 500, 2400, 1080)            # paysage dans un rectangle plus large
    assert r.height() == pytest.approx(450) and r.width() == pytest.approx(1000)
    assert fit_rect(0, 100, 10, 10).isEmpty() and fit_rect(100, 100, 0, 10).isEmpty()


@pytest.mark.parametrize("cls", [SoftwareVideoWidget, GLVideoWidget])
def test_aspect_ratio_preserved_with_black_bars(cls):
    w = cls()
    w.resize(400, 400)
    w.show()
    w.set_frame(solid(100, 200, (255, 0, 0)))
    img = w.grab().toImage() if cls is SoftwareVideoWidget else w.grabFramebuffer()
    if cls is GLVideoWidget and img.isNull():
        # Limite de l'environnement, pas de l'application : sans contexte OpenGL (Windows « offscreen » sur un serveur de CI
        # sans carte graphique) il n'y a aucun rendu à lire. L'application passe alors au rendu logiciel
        # (DisplayWindow.ensure_valid_gl) ; la même vérification est faite sur SoftwareVideoWidget.
        pytest.skip("pas de contexte OpenGL dans cet environnement : rien à lire")
    assert img.pixelColor(10, 200) == QColor(0, 0, 0)           # bande gauche noire
    assert img.pixelColor(390, 200) == QColor(0, 0, 0)          # bande droite noire
    c = img.pixelColor(200, 200)
    assert c.red() > 240 and c.green() < 15 and c.blue() < 15   # image au centre
    assert w.has_frame and w.message == ""


def test_scaled_frame_keeps_native_aspect():
    """Image déjà réduite par le décodeur : le rapport vient de la taille native."""
    w = SoftwareVideoWidget()
    w.resize(300, 300)
    w.show()
    w.set_frame(solid(50, 100, (0, 255, 0), native=(1080, 2160)))
    w.grab()
    assert w.last_rect.width() == pytest.approx(150) and w.last_rect.height() == pytest.approx(300)


def test_message_overlay_and_clear():
    w = SoftwareVideoWidget()
    w.resize(500, 300)
    w.show()
    w.set_message("Téléphone débranché")
    img = w.grab().toImage()
    assert any(img.pixelColor(x, 150) != QColor(0, 0, 0) for x in range(150, 350, 4))   # le cadre du message est dessiné
    w.set_frame(solid(10, 10, (0, 0, 255)))
    assert w.message == ""
    w.clear("Fin")
    assert not w.has_frame and w.message == "Fin"


def test_target_pixels_follow_resize_and_signal():
    w = SoftwareVideoWidget()
    seen = []
    w.resized.connect(lambda a, b: seen.append((a, b)))
    w.resize(640, 360)
    w.show()
    w.resize(800, 450)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert w.target_pixels[0] >= 800 and seen and seen[-1] == w.target_pixels


def test_env_forces_software(monkeypatch):
    monkeypatch.setenv("ANDROMIRROR_NO_GL", "1")
    assert isinstance(create_video_widget(), SoftwareVideoWidget)
    monkeypatch.delenv("ANDROMIRROR_NO_GL")
    assert isinstance(create_video_widget(), GLVideoWidget)
