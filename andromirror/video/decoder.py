"""Décodage vidéo (H.264 / H.265) avec PyAV (FFmpeg) : accélération matérielle si possible, sinon logiciel.

VideoDecoder reçoit des « unités d'accès » complètes (une image encodée par appel) et retourne des images
RGB prêtes à afficher, déjà réduites à la taille de la fenêtre pour économiser le processeur.

Accélération matérielle : essayée dans l'ordre (d3d11va, dxva2, cuda, qsv sous Windows) parmi celles que cette
version de FFmpeg propose. Si l'accélération échoue (création ou décodage), on bascule définitivement sur le
décodage logiciel et on le note dans le journal. Rien n'est jamais envoyé en dehors du PC.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..logging_setup import get_logger

log = get_logger("decodeur")

HW_PREFERENCE_WINDOWS = ["d3d11va", "dxva2", "cuda", "qsv"]
HW_PREFERENCE_OTHER = ["vaapi", "cuda", "qsv", "videotoolbox"]


class DecoderError(Exception):
    """Le décodeur ne peut pas fonctionner (par ex. PyAV absent)."""


@dataclass(frozen=True)
class DecodedFrame:
    width: int            # taille de `data` (peut être plus petite que la vidéo : réduite pour la fenêtre)
    height: int
    stride: int           # octets par ligne
    data: bytes           # RGB 8 bits par canal (format « rgb24 »)
    pts_us: int
    native_width: int     # taille réelle de la vidéo décodée
    native_height: int
    received_at: float = 0.0     # horodatage de réception du paquet (latence)
    decoded_at: float = 0.0


def fit_size(native: Tuple[int, int], target: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    """Taille de sortie : tient dans `target`, même rapport largeur/hauteur, jamais agrandie, valeurs paires."""
    nw, nh = native
    if not target or target[0] <= 0 or target[1] <= 0:
        return nw, nh
    scale = min(target[0] / nw, target[1] / nh, 1.0)
    w = max(2, int(nw * scale) // 2 * 2)
    h = max(2, int(nh * scale) // 2 * 2)
    return w, h


def hw_candidates() -> List[str]:
    """Accélérations matérielles proposées par cette version de FFmpeg, dans l'ordre de préférence."""
    try:
        from av.codec.hwaccel import hwdevices_available
        available = set(hwdevices_available())
    except Exception:  # noqa: BLE001
        return []
    pref = HW_PREFERENCE_WINDOWS if sys.platform.startswith("win") else HW_PREFERENCE_OTHER
    return [name for name in pref if name in available]


class VideoDecoder:
    """Un décodeur à la fois ; reset() après chaque changement de taille ou de codec."""

    def __init__(self, codec: str = "h264", prefer_hw: bool = True,
                 target_size: Optional[Callable[[], Optional[Tuple[int, int]]]] = None):
        try:
            import av  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise DecoderError(f"PyAV (FFmpeg) est introuvable : {exc}") from exc
        if codec not in ("h264", "hevc"):
            raise DecoderError(f"codec non pris en charge : {codec}")
        self.codec = codec
        self.prefer_hw = prefer_hw and os.environ.get("ANDROMIRROR_NO_HW") != "1"
        self._target = target_size
        self._ctx = None
        self.hw_name: Optional[str] = None          # accélération réellement utilisée (None = logiciel)
        self._hw_failed = False
        self._hw_verified = False
        self._open()

    # -- ouverture ------------------------------------------------------------------------------
    def _make_context(self, hw_name: Optional[str]):
        import av
        hwaccel = None
        if hw_name:
            from av.codec.hwaccel import HWAccel
            hwaccel = HWAccel(device_type=hw_name, allow_software_fallback=False)
        ctx = av.CodecContext.create(self.codec, "r", hwaccel=hwaccel) if hwaccel else \
            av.CodecContext.create(self.codec, "r")
        # Faible latence : pas de réordonnancement, pas de décodage « en retard » sur plusieurs images.
        ctx.options = {"flags": "low_delay", "flags2": "fast"}
        ctx.thread_type = "SLICE"
        ctx.thread_count = max(1, min(4, os.cpu_count() or 1))
        ctx.open()
        return ctx

    def _open(self) -> None:
        self._ctx = None
        self.hw_name = None
        self._hw_verified = False
        if self.prefer_hw and not self._hw_failed:
            for name in hw_candidates():
                try:
                    ctx = self._make_context(name)
                    # PyAV peut ouvrir le décodeur sans l'accélération (appareil absent) : on vérifie.
                    if not getattr(ctx, "is_hwaccel", False):
                        log.info("Accélération %s non activée par FFmpeg (aucun appareil compatible)", name)
                        continue
                    self._ctx = ctx
                    self.hw_name = name
                    log.info("Décodage matériel demandé : %s (vérifié au premier décodage)", name)
                    return
                except Exception as exc:  # noqa: BLE001
                    log.info("Accélération %s indisponible : %s", name, exc)
        self._ctx = self._make_context(None)
        log.info("Décodage logiciel (%s)", self.codec)

    def reset(self) -> None:
        """Repart de zéro (changement de taille : l'ancien décodeur garderait l'ancienne taille)."""
        self._open()

    @property
    def description(self) -> str:
        return f"matériel ({self.hw_name})" if self.hw_name else "logiciel"

    # -- décodage -----------------------------------------------------------------------------------
    def decode(self, data: bytes, pts_us: int = 0, received_at: float = 0.0) -> List[DecodedFrame]:
        """Décode une unité d'accès. Retourne 0, 1 ou plusieurs images. Lève DecoderError si irrécupérable."""
        import av
        if not data:
            return []        # un paquet vide « viderait » le décodeur (fin de flux) : jamais envoyé
        try:
            frames = self._ctx.decode(av.Packet(data))
        except av.error.InvalidDataError as exc:
            # Flux abîmé : on laisse le décodeur reprendre à la prochaine image clé.
            log.warning("Données vidéo invalides ignorées : %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            if self.hw_name:
                log.warning("Échec du décodage matériel (%s) : %s. Passage au décodage logiciel.",
                            self.hw_name, exc)
                self._hw_failed = True
                self._open()
                return []        # l'appelant demandera une image clé
            raise DecoderError(f"décodage impossible : {exc}") from exc
        out = [self._convert(f, pts_us, received_at) for f in frames]
        if out and self.hw_name and not self._hw_verified:
            self._hw_verified = True
            log.info("Décodage matériel confirmé (%s)", self.hw_name)
        return out

    def _convert(self, frame, pts_us: int, received_at: float) -> DecodedFrame:
        from av.video.reformatter import Colorspace
        nw, nh = frame.width, frame.height
        w, h = fit_size((nw, nh), self._target() if self._target else None)
        kwargs = {"format": "rgb24"}
        if (w, h) != (nw, nh):
            kwargs.update(width=w, height=h, interpolation="AREA")
        # MediaCodec ne précise pas toujours l'espace colorimétrique : BT.709 pour le HD, comme les lecteurs vidéo.
        try:
            if int(frame.colorspace) in (0, 2) and nh >= 720:    # 0 = RGB, 2 = non spécifié
                kwargs["src_colorspace"] = Colorspace.ITU709
        except Exception:  # noqa: BLE001
            pass
        rgb = frame.reformat(**kwargs)
        plane = rgb.planes[0]
        return DecodedFrame(width=rgb.width, height=rgb.height, stride=plane.line_size, data=bytes(plane),
                            pts_us=pts_us, native_width=nw, native_height=nh,
                            received_at=received_at, decoded_at=time.monotonic())

    def close(self) -> None:
        self._ctx = None
