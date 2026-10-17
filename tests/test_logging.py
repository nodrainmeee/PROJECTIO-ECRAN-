import sys
import threading

from andromirror import logging_setup, paths


def test_log_file_written(_isolated_home):
    logging_setup.setup_logging()
    logging_setup.get_logger("test").error("Problème de test é à ✓")
    logging_setup.close_logging()
    text = paths.log_path().read_text(encoding="utf-8")
    assert "Problème de test é à ✓" in text and "[ERROR]" in text
    assert str(paths.log_path()).startswith(str(_isolated_home))


def test_setup_is_idempotent(_isolated_home):
    a = logging_setup.setup_logging()
    n = len(a.handlers)
    b = logging_setup.setup_logging()
    assert a is b and len(b.handlers) == n
    logging_setup.close_logging()


def test_excepthooks_log_and_notify(_isolated_home):
    logging_setup.setup_logging()
    seen = []
    old_hook, old_thread = sys.excepthook, threading.excepthook
    try:
        logging_setup.install_excepthooks(on_error=seen.append)
        try:
            raise RuntimeError("boum")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        def boom():
            raise ValueError("boum thread")
        t = threading.Thread(target=boom, name="t-test")
        t.start()
        t.join()
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread
    logging_setup.close_logging()
    text = paths.log_path().read_text(encoding="utf-8")
    assert "boum" in text and "boum thread" in text and "Traceback" in text
    assert len(seen) == 2
