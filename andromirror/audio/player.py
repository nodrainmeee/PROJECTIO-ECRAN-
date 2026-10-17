"""Lecture du son du téléphone sur les haut-parleurs du PC.

Le téléphone envoie du PCM 16 bits (paquets T_AUDIO). AudioPlayer garde une petite file d'attente (~80 ms) :
assez pour absorber les irrégularités du câble, assez courte pour que le son reste synchronisé avec l'image.
  * file trop pleine (retard accumulé) : on jette les plus vieux échantillons pour rattraper ;
  * file vide (coupure) : silence, puis on recommence à lire quand ~60 ms sont revenus.

La bibliothèque de sortie (sounddevice / PortAudio) n'est importée qu'au démarrage : si elle manque ou si aucune
carte son n'est disponible, start() retourne False avec un message en français, et l'image continue sans le son.
"""
from __future__ import annotations

import threading
from array import array
from typing import Any, Callable, Optional

from ..logging_setup import get_logger
from ..video.protocol import AudioFormat

log = get_logger("son")

TARGET_MS = 80        # file visée après un rattrapage
MAX_MS = 250          # au-delà : on rattrape
PREFILL_MS = 60       # avant de (re)démarrer la lecture
DEVICE_LATENCY_S = 0.08


def _default_stream_factory(fmt: AudioFormat, callback: Callable) -> Any:
    import sounddevice as sd  # noqa: PLC0415 - import tardif volontaire
    stream = sd.RawOutputStream(samplerate=fmt.sample_rate, channels=fmt.channels, dtype="int16",
                                latency=DEVICE_LATENCY_S, callback=callback)
    stream.start()
    return stream


class AudioPlayer:
    def __init__(self, volume: int = 100, stream_factory: Optional[Callable[[AudioFormat, Callable], Any]] = None):
        self._factory = stream_factory or _default_stream_factory
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._fmt: Optional[AudioFormat] = None
        self._stream: Any = None
        self._playing = False
        self._gain = 1.0
        self._table: Optional[list] = None
        self.error = ""
        self.underruns = 0
        self.dropped_bytes = 0
        self.bytes_pushed = 0
        self.set_volume(volume)

    # -- réglages ------------------------------------------------------------------------------------------------
    def set_volume(self, percent: int) -> None:
        """0..100. Courbe douce (carré) : 50 % de curseur = 25 % d'amplitude, plus proche de l'oreille."""
        p = max(0, min(100, int(percent))) / 100.0
        gain = p * p
        with self._lock:
            if abs(gain - self._gain) < 1e-9 and (self._table is not None or gain == 1.0):
                return
            self._gain = gain
            self._table = None if gain == 1.0 else [max(-32768, min(32767, int((i - 32768) * gain)))
                                                    for i in range(65536)]

    @property
    def volume_gain(self) -> float:
        return self._gain

    @property
    def active(self) -> bool:
        return self._stream is not None

    # -- cycle de vie ----------------------------------------------------------------------------------------------
    def start(self, fmt: AudioFormat) -> bool:
        """Ouvre la sortie audio. Retourne False (et remplit self.error) si c'est impossible."""
        self.stop()
        self._fmt = fmt
        try:
            self._stream = self._factory(fmt, self._callback)
        except ImportError:
            self.error = "Le composant de lecture du son est absent de cette installation."
        except Exception as exc:  # noqa: BLE001 - aucune carte son, carte occupée, format refusé...
            self.error = f"Impossible d'ouvrir la sortie audio de l'ordinateur ({exc})."
        if self._stream is None:
            log.error("Son désactivé : %s", self.error)
            return False
        self.error = ""
        log.info("Sortie audio ouverte : %d Hz, %d canaux", fmt.sample_rate, fmt.channels)
        return True

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        with self._lock:
            self._buf.clear()
            self._playing = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Fermeture de la sortie audio")

    # -- données ---------------------------------------------------------------------------------------------------------
    def push(self, pcm: bytes) -> None:
        """Ajoute des échantillons reçus du téléphone (thread de réception)."""
        fmt = self._fmt
        if fmt is None or self._stream is None or not pcm:
            return
        fb = fmt.frame_bytes
        pcm = pcm[: len(pcm) // fb * fb]               # jamais de trame coupée en deux
        with self._lock:
            table = self._table
        if table is not None:
            samples = array("h")
            samples.frombytes(pcm)
            pcm = array("h", [table[s + 32768] for s in samples]).tobytes()
        with self._lock:
            self._buf += pcm
            self.bytes_pushed += len(pcm)
            limit = fmt.sample_rate * fb * MAX_MS // 1000
            if len(self._buf) > limit:
                keep = fmt.sample_rate * fb * TARGET_MS // 1000
                drop = (len(self._buf) - keep) // fb * fb
                del self._buf[:drop]
                self.dropped_bytes += drop

    def buffered_ms(self) -> float:
        fmt = self._fmt
        if fmt is None:
            return 0.0
        with self._lock:
            return len(self._buf) / (fmt.sample_rate * fmt.frame_bytes) * 1000.0

    # -- appelé par la carte son (thread audio) ---------------------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status) -> None:
        fmt = self._fmt
        if fmt is None:
            return
        need = frames * fmt.frame_bytes
        with self._lock:
            if not self._playing:
                if len(self._buf) >= fmt.sample_rate * fmt.frame_bytes * PREFILL_MS // 1000:
                    self._playing = True
            if self._playing and len(self._buf) >= need:
                chunk = bytes(self._buf[:need])
                del self._buf[:need]
            elif self._playing:
                chunk = bytes(self._buf) + bytes(need - len(self._buf))    # fin de file : complété par du silence
                self._buf.clear()
                self._playing = False
                self.underruns += 1
            else:
                chunk = bytes(need)
        outdata[:need] = chunk
