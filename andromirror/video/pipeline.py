"""Chaîne de traitement à faible latence : paquets reçus → décodage → dernière image à afficher.

Principe : on préfère afficher l'image la plus récente plutôt que de rattraper un retard.
  * PacketQueue : file bornée entre le thread réseau et le décodeur. Si le décodeur prend du retard
    (trop de paquets en attente), on jette les paquets vidéo en attente, on attend la prochaine image
    clé (sinon l'image serait déformée) et on demande une image clé au téléphone.
  * LatestFrame : une seule case pour l'image décodée ; une nouvelle image remplace l'ancienne non affichée.
  * DecodeWorker : thread de décodage ; gère aussi les changements de taille/rotation et les messages.
Aucune dépendance à Qt : tout est testable avec de simples threads.
"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

from ..logging_setup import get_logger
from . import protocol as P
from .decoder import DecodedFrame, DecoderError, VideoDecoder

log = get_logger("flux")

DEFAULT_MAX_QUEUE = 24             # ~0,4 s à 60 images/s
KEYFRAME_REQUEST_MIN_INTERVAL = 0.5


class PacketQueue:
    """File de paquets avec abandon des anciens paquets vidéo en cas de retard."""

    def __init__(self, max_packets: int = DEFAULT_MAX_QUEUE,
                 request_keyframe: Optional[Callable[[], None]] = None):
        self._max = max(2, max_packets)
        self._items: Deque[P.Packet] = collections.deque()
        self._cond = threading.Condition()
        self._request_keyframe = request_keyframe
        self._waiting_key = False
        self._last_request = 0.0
        self._closed = False
        self.dropped_packets = 0
        self.overflows = 0

    @property
    def waiting_for_keyframe(self) -> bool:
        return self._waiting_key

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def put(self, pkt: P.Packet) -> None:
        request = False
        with self._cond:
            if self._closed:
                return
            accept = True
            if pkt.type == P.T_VIDEO and not pkt.is_config:
                if self._waiting_key:
                    if pkt.is_key:
                        self._waiting_key = False      # image clé : on repart proprement
                    else:
                        accept = False
                elif len(self._items) >= self._max:
                    self._flush_video_locked()          # retard : on jette tout ce qui attend
                    self.overflows += 1
                    if not pkt.is_key:
                        self._waiting_key = True
                        accept = False
                if not accept:
                    self.dropped_packets += 1
                    request = self._due_for_request()
            elif pkt.type == P.T_SIZE:
                # Nouvelle taille : tout ce qui précède est obsolète. La nouvelle config suivra.
                self._flush_video_locked()
                self._waiting_key = False
            if accept:
                self._items.append(pkt)
                self._cond.notify()
        if request and self._request_keyframe:
            self._safe_request()

    def _safe_request(self) -> None:
        try:
            self._request_keyframe()
        except Exception:  # noqa: BLE001
            log.exception("Demande d'image clé impossible")

    def _due_for_request(self) -> bool:
        now = time.monotonic()
        if now - self._last_request >= KEYFRAME_REQUEST_MIN_INTERVAL:
            self._last_request = now
            return True
        return False

    def _flush_video_locked(self) -> None:
        kept = collections.deque()
        for p in self._items:
            if p.type == P.T_VIDEO and not p.is_config:
                self.dropped_packets += 1
            else:
                kept.append(p)
        self._items = kept

    def get(self, timeout: float = 0.2) -> Optional[P.Packet]:
        with self._cond:
            if not self._items and not self._closed:
                self._cond.wait(timeout)
            if self._items:
                return self._items.popleft()
            return None

    @property
    def closed(self) -> bool:
        return self._closed


class LatestFrame:
    """Une seule case : une nouvelle image remplace l'ancienne si celle-ci n'a pas été affichée."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: Optional[DecodedFrame] = None
        self.replaced = 0       # images jamais affichées car remplacées par une plus récente
        self.published = 0

    def put(self, frame: DecodedFrame) -> bool:
        """Retourne True si la case était vide (le consommateur doit être prévenu)."""
        with self._lock:
            was_empty = self._frame is None
            if not was_empty:
                self.replaced += 1
            self._frame = frame
            self.published += 1
            return was_empty

    def take(self) -> Optional[DecodedFrame]:
        with self._lock:
            f, self._frame = self._frame, None
            return f


@dataclass
class Stats:
    frames_decoded: int = 0
    frames_shown: int = 0
    frames_replaced: int = 0
    packets_dropped: int = 0
    queue_overflows: int = 0
    fps: float = 0.0
    latency_ms: float = 0.0             # réception → fin du décodage (dernière image)
    decoder: str = "—"
    width: int = 0
    height: int = 0
    _times: Deque[float] = field(default_factory=lambda: collections.deque(maxlen=120), repr=False)


class DecodeWorker(threading.Thread):
    """Thread de décodage. Les rappels sont appelés depuis CE thread."""

    def __init__(self, queue: PacketQueue, latest: LatestFrame, codec: str, prefer_hw: bool = True,
                 target_size: Optional[Callable] = None,
                 request_keyframe: Optional[Callable[[], None]] = None,
                 on_frame: Optional[Callable[[], None]] = None,
                 on_size: Optional[Callable[[int, int, int], None]] = None,
                 on_message: Optional[Callable[[str, bool], None]] = None,
                 on_decoder: Optional[Callable[[str], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 decoder_factory: Optional[Callable[..., VideoDecoder]] = None):
        super().__init__(name="andromirror-decodage", daemon=True)
        self.queue, self.latest = queue, latest
        self.stats = Stats()
        self._codec, self._prefer_hw, self._target_fn = codec, prefer_hw, target_size
        self._request_keyframe = request_keyframe
        self._on_frame, self._on_size, self._on_message = on_frame, on_size, on_message
        self._on_decoder, self._on_error = on_decoder, on_error
        self._factory = decoder_factory or VideoDecoder
        self._halt = threading.Event()
        self._config = b""
        self._last_keyframe_request = 0.0

    def stop(self) -> None:
        self._halt.set()
        self.queue.close()

    # -- boucle ---------------------------------------------------------------------------------------
    def run(self) -> None:
        try:
            decoder = self._factory(self._codec, prefer_hw=self._prefer_hw, target_size=self._target_fn)
        except Exception as exc:  # noqa: BLE001
            log.error("Décodeur impossible : %s", exc)
            if self._on_error:
                self._on_error(f"Le décodeur vidéo ne peut pas démarrer : {exc}")
            return
        self.stats.decoder = decoder.description
        if self._on_decoder:
            self._on_decoder(decoder.description)
        try:
            while not self._halt.is_set():
                pkt = self.queue.get(0.2)
                if pkt is None:
                    if self.queue.closed:
                        break
                    continue
                self._process(decoder, pkt)
        except DecoderError as exc:
            log.error("Décodage arrêté : %s", exc)
            if self._on_error:
                self._on_error(f"Le décodage vidéo a échoué : {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("Erreur inattendue dans le décodage")
            if self._on_error:
                self._on_error(f"Erreur inattendue dans le décodage : {exc}")
        finally:
            decoder.close()

    def _process(self, decoder: VideoDecoder, pkt: P.Packet) -> None:
        if pkt.type == P.T_SIZE:
            w, h, rot = P.parse_size(pkt.payload)
            log.info("Changement d'écran : %dx%d rotation %d", w, h, rot)
            self._config = b""
            decoder.reset()
            self.stats.width, self.stats.height = w, h
            if self._on_size:
                self._on_size(w, h, rot)
            return
        if pkt.type == P.T_MESSAGE:
            text = pkt.payload.decode("utf-8", errors="replace")
            log.info("Message du serveur : %s", text)
            if self._on_message:
                self._on_message(text, bool(pkt.flags & P.F_FATAL))
            return
        if pkt.is_config:
            self._config = pkt.payload
            return
        data = pkt.payload
        if pkt.is_key and self._config:
            data = self._config + data       # le décodeur doit voir SPS/PPS juste avant l'image clé
        before_hw = decoder.hw_name
        before_damaged = getattr(decoder, "damaged_packets", 0)
        frames = decoder.decode(data, pkt.pts_us, pkt.received_at)
        if getattr(decoder, "damaged_packets", 0) != before_damaged and not (before_hw and decoder.hw_name is None):
            self._request_keyframe_limited()      # flux abîmé : repart d'une image clé
        if before_hw and decoder.hw_name is None:
            # bascule matériel → logiciel : le nouveau décodeur n'a pas vu l'image clé
            self.stats.decoder = decoder.description
            if self._on_decoder:
                self._on_decoder(decoder.description)
            if self._request_keyframe:
                self._request_keyframe()
        for fr in frames:
            self._publish(fr)

    def _request_keyframe_limited(self) -> None:
        now = time.monotonic()
        if self._request_keyframe and now - self._last_keyframe_request >= KEYFRAME_REQUEST_MIN_INTERVAL:
            self._last_keyframe_request = now
            self._request_keyframe()

    def _publish(self, fr: DecodedFrame) -> None:
        st = self.stats
        st.frames_decoded += 1
        now = time.monotonic()
        st._times.append(now)
        if len(st._times) >= 2 and st._times[-1] > st._times[0]:
            st.fps = (len(st._times) - 1) / (st._times[-1] - st._times[0])
        if fr.received_at:
            st.latency_ms = (fr.decoded_at - fr.received_at) * 1000.0
        st.packets_dropped = self.queue.dropped_packets
        st.queue_overflows = self.queue.overflows
        if self.latest.put(fr) and self._on_frame:
            self._on_frame()
        st.frames_replaced = self.latest.replaced
