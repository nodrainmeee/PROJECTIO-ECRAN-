"""Contrôle du téléphone : conversion de points, touches, contrôleur, envoi réel vers un faux serveur."""
import threading
import time

import pytest

from andromirror.control import keymap
from andromirror.control.controller import InputController
from andromirror.control.mapping import widget_to_video
from andromirror.video import protocol as P

RECT = (10.0, 0.0, 180.0, 400.0)       # image dessinée dans un widget 200 x 400 (bandes noires de 10 px)


# --------------------------------------------------------------------------- conversion de points
def test_center_maps_to_center_of_video():
    assert widget_to_video(100, 200, RECT, 1080, 2400) == (540, 1200)


def test_black_bars_are_ignored_unless_clamped():
    assert widget_to_video(5, 200, RECT, 1080, 2400) is None
    assert widget_to_video(5, 200, RECT, 1080, 2400, clamp=True) == (0, 1200)


def test_bottom_right_corner_stays_inside_the_video():
    assert widget_to_video(190, 400, RECT, 1080, 2400) == (1079, 2399)
    assert widget_to_video(500, 900, RECT, 1080, 2400, clamp=True) == (1079, 2399)


@pytest.mark.parametrize("rect,vw,vh", [((0, 0, 0, 10), 100, 100), ((0, 0, 10, 10), 0, 100)])
def test_degenerate_geometry_gives_none(rect, vw, vh):
    assert widget_to_video(1, 1, rect, vw, vh, clamp=True) is None


# --------------------------------------------------------------------------- touches
def test_special_keys_map_to_android_codes():
    assert keymap.android_keycode(0x01000004) == keymap.KEYCODE_ENTER       # Entrée
    assert keymap.android_keycode(0x01000003) == keymap.KEYCODE_DEL         # Retour arrière
    assert keymap.android_keycode(0x01000012) == keymap.KEYCODE_DPAD_LEFT
    assert keymap.android_keycode(0x41) == 0                                # « A » : c'est du texte, pas une touche


def test_is_typable():
    assert keymap.is_typable("a") and keymap.is_typable("é") and keymap.is_typable(" ")
    assert not keymap.is_typable("\r") and not keymap.is_typable("\x08") and not keymap.is_typable("")


# --------------------------------------------------------------------------- contrôleur
def make():
    sent = []
    return InputController(sent.append), sent


def test_touch_sequence():
    c, sent = make()
    c.touch_down(5, 6, 100, 200)
    c.touch_move(7, 8, 100, 200)
    c.touch_up(7, 8, 100, 200)
    assert sent == [P.pack_touch(P.TOUCH_DOWN, 5, 6, 100, 200), P.pack_touch(P.TOUCH_MOVE, 7, 8, 100, 200),
                    P.pack_touch(P.TOUCH_UP, 7, 8, 100, 200)]
    assert not c.touching


def test_move_and_up_without_down_are_ignored():
    c, sent = make()
    c.touch_move(1, 1, 10, 10)
    c.touch_up(1, 1, 10, 10)
    assert sent == []


def test_disabled_controller_sends_nothing_and_releases_everything():
    c, sent = make()
    c.touch_down(1, 1, 10, 10)
    c.key_down(0x01000003, "\x08", False, False)
    sent.clear()
    c.set_enabled(False)
    assert P.pack_key(P.KEY_UP, keymap.KEYCODE_DEL) in sent           # la touche maintenue est relâchée
    assert not c.touching
    sent.clear()
    c.touch_down(1, 1, 10, 10)
    c.press(keymap.KEYCODE_BACK)
    c.type_text("a")
    c.scroll(1, 1, 10, 10, 0, 1)
    assert sent == []


def test_printable_text_is_typed_special_keys_are_keys():
    c, sent = make()
    c.key_down(0x41, "a", False, False)
    c.key_down(0x01000004, "\r", False, False)
    c.key_up(0x01000004)
    assert sent == [P.pack_text("a"), P.pack_key(P.KEY_DOWN, keymap.KEYCODE_ENTER),
                    P.pack_key(P.KEY_UP, keymap.KEYCODE_ENTER)]


def test_ctrl_shortcuts_are_not_typed_but_altgr_is():
    c, sent = make()
    c.key_down(0x43, "\x03", True, False)          # Ctrl+C : ignoré
    c.key_down(0x43, "c", True, False)             # Ctrl + lettre : ignoré
    assert sent == []
    c.key_down(0x40, "@", True, True)              # AltGr (Ctrl+Alt) : du texte
    assert sent == [P.pack_text("@")]


def test_key_up_without_matching_down_sends_nothing():
    c, sent = make()
    c.key_up(0x01000003)
    assert sent == []


def test_press_sends_down_then_up():
    c, sent = make()
    c.press(keymap.KEYCODE_HOME)
    assert sent == [P.pack_key(P.KEY_DOWN, 3), P.pack_key(P.KEY_UP, 3)]


def test_scroll_converts_notches_to_units():
    c, sent = make()
    c.scroll(10, 20, 100, 200, 0.0, -1.5)
    assert sent == [P.pack_scroll(10, 20, 100, 200, 0, -24)]
    sent.clear()
    c.scroll(10, 20, 100, 200, 0.0, 0.0)
    assert sent == []


def test_send_errors_never_propagate():
    def boom(_):
        raise OSError("coupé")
    c = InputController(boom)
    c.touch_down(1, 1, 10, 10)
    c.press(4)


# --------------------------------------------------------------------------- vers un faux serveur (socket réel)
def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_commands_reach_the_server_intact_even_when_bytes_look_like_other_commands():
    from andromirror.video.receiver import StreamReceiver
    from fake_server import FakeServer
    srv = FakeServer(sizes=[(160, 320)], frames_per_size=5).start()
    rx = StreamReceiver(srv.port, bytes(16), lambda h: None, lambda p: None)
    t = threading.Thread(target=rx.run, daemon=True)
    t.start()
    try:
        assert _wait(lambda: rx.hello is not None)
        c = InputController(rx.send_command)
        c.touch_down(1, 2, 1080, 2400)          # les valeurs 1 et 2 = codes de « image clé » et « quitter »
        c.touch_move(2, 1, 1080, 2400)
        c.press(2)
        c.key_down(0x41, "é", False, False)
        c.touch_up(2, 1, 1080, 2400)
        expected = [P.pack_touch(P.TOUCH_DOWN, 1, 2, 1080, 2400), P.pack_touch(P.TOUCH_MOVE, 2, 1, 1080, 2400),
                    P.pack_key(P.KEY_DOWN, 2), P.pack_key(P.KEY_UP, 2), P.pack_text("é"),
                    P.pack_touch(P.TOUCH_UP, 2, 1, 1080, 2400)]
        assert _wait(lambda: len(srv.commands) >= len(expected))
        assert srv.commands == expected
        assert srv.bad_commands == 0 and not srv.quit_received and srv.keyframe_requests == 0
    finally:
        rx.stop()
        t.join(5)
        srv.close()
