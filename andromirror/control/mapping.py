"""Conversion d'un point de la fenêtre en point de la vidéo (pixels du téléphone), sans dépendance à Qt."""
from __future__ import annotations

from typing import Optional, Tuple


def widget_to_video(px: float, py: float, rect: Tuple[float, float, float, float], video_w: int, video_h: int,
                    clamp: bool = False) -> Optional[Tuple[int, int]]:
    """(px, py) : point dans le widget ; rect : (x, y, largeur, hauteur) de l'image dessinée dans le widget.

    Retourne le point correspondant dans la vidéo (0 <= x < video_w, 0 <= y < video_h), ou None si le point est
    en dehors de l'image (bandes noires) et que `clamp` est faux. Avec `clamp`, le point est ramené au bord le
    plus proche (utile pour continuer un glissé qui sort de l'image).
    """
    rx, ry, rw, rh = rect
    if rw <= 0 or rh <= 0 or video_w <= 0 or video_h <= 0:
        return None
    fx = (px - rx) / rw
    fy = (py - ry) / rh
    if not clamp and not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return None
    fx = min(1.0, max(0.0, fx))
    fy = min(1.0, max(0.0, fy))
    x = min(video_w - 1, int(fx * video_w))
    y = min(video_h - 1, int(fy * video_h))
    return x, y
