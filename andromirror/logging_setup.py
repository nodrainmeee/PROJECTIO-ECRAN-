"""Journal d'erreurs (fichier) et capture des exceptions non gérées."""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Callable, Optional

from . import paths

LOGGER_NAME = "andromirror"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure le journal fichier (rotation, UTF-8). Idempotent."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    target = str(paths.log_path())
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler) and h.baseFilename == _abs(target):
            return logger
        logger.removeHandler(h)
        h.close()
    try:
        handler = RotatingFileHandler(target, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    except OSError:
        # Impossible d'écrire le journal : on continue sans planter.
        logger.addHandler(logging.NullHandler())
    return logger


def _abs(p: str) -> str:
    import os
    return os.path.abspath(p)


def close_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def install_excepthooks(on_error: Optional[Callable[[str], None]] = None) -> None:
    """Écrit toute exception non gérée dans le journal (et prévient l'UI)."""
    log = get_logger("crash")

    def _report(exc_type, exc, tb, origin: str) -> None:
        log.critical("Exception non gérée (%s)", origin, exc_info=(exc_type, exc, tb))
        if on_error is not None:
            try:
                on_error(f"{exc_type.__name__}: {exc}")
            except Exception:  # ne jamais planter dans le gestionnaire d'erreurs
                log.exception("Échec de l'affichage de l'erreur")

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _report(exc_type, exc, tb, "thread principal")

    def _thread_hook(args):
        _report(args.exc_type, args.exc_value, args.exc_traceback, f"thread {args.thread.name if args.thread else '?'}")

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
