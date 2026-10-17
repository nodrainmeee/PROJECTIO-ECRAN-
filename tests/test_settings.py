import json

import pytest

from andromirror.settings import SettingsStore, defaults


def test_defaults_when_no_file(tmp_path):
    s = SettingsStore(tmp_path / "s.json").load()
    assert s.as_dict() == defaults()
    assert s.get("quality", "bitrate_mbps") == 16 and s.get("general", "theme") == "system"


def test_autosave_and_reload(tmp_path):
    p = tmp_path / "s.json"
    s = SettingsStore(p).load()
    s.set("quality", "fps", 30)
    s.set("general", "theme", "dark")
    s.set("audio", "volume", 40)
    assert p.exists()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["quality"]["fps"] == 30 and raw["version"] == 1
    s2 = SettingsStore(p).load()
    assert (s2.get("quality", "fps"), s2.get("general", "theme"), s2.get("audio", "volume")) == (30, "dark", 40)
    assert not (tmp_path / "s.json.tmp").exists()


def test_set_validates():
    s = SettingsStore(autosave=False)
    assert s.set("quality", "bitrate_mbps", 500) == 60       # borné
    assert s.set("quality", "bitrate_mbps", 1) == 4
    with pytest.raises(ValueError):
        s.set("quality", "codec", "vp9")
    with pytest.raises(ValueError):
        s.set("general", "auto_reconnect", "oui")
    with pytest.raises(ValueError):
        s.set("quality", "fps", 45)
    with pytest.raises(ValueError):
        s.set("audio", "volume", True)


def test_corrupt_file_is_quarantined(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{pas du json", encoding="utf-8")
    s = SettingsStore(p).load()
    assert s.as_dict() == defaults()
    assert (tmp_path / "s.json.corrompu").exists() and not p.exists()


def test_invalid_values_reset_individually(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"quality": {"fps": 999, "codec": "h265", "bitrate_mbps": "beaucoup"},
                             "general": {"theme": "dark", "inconnu": 1}, "bidon": {}}), encoding="utf-8")
    s = SettingsStore(p).load()
    assert s.get("quality", "fps") == 60            # invalide -> défaut
    assert s.get("quality", "codec") == "h265"      # valide -> conservé
    assert s.get("quality", "bitrate_mbps") == 16
    assert s.get("general", "theme") == "dark"


def test_non_dict_root(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    assert SettingsStore(p).load().as_dict() == defaults()


def test_listeners_and_reset(tmp_path):
    s = SettingsStore(tmp_path / "s.json").load()
    events = []
    s.subscribe(lambda sec, k, v: events.append((sec, k, v)))
    s.set("general", "theme", "light")
    s.set("general", "theme", "light")   # inchangé : pas d'événement
    assert events == [("general", "theme", "light")]
    s.reset_section("general")
    assert s.get("general", "theme") == "system"


def test_save_failure_does_not_crash(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    s = SettingsStore(blocker / "sub" / "s.json")   # le « dossier » est un fichier
    s.set("quality", "fps", 30)
    assert s.last_save_ok is False


def test_default_path_uses_user_home(_isolated_home):
    s = SettingsStore().load()
    s.set("quality", "fps", 30)
    assert (_isolated_home / "settings.json").exists()


def test_hw_decode_setting_default_validation_and_persistence(tmp_path):
    s = SettingsStore(tmp_path / "s.json").load()
    assert s.get("quality", "hw_decode") is True
    with pytest.raises(ValueError):
        s.set("quality", "hw_decode", "oui")
    s.set("quality", "hw_decode", False)
    assert SettingsStore(tmp_path / "s.json").load().get("quality", "hw_decode") is False
