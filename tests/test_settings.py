from tokitty.settings import Settings, load_settings, save_settings


def test_default_tray_enabled_true(tmp_path):
    assert load_settings(tmp_path).tray_enabled is True


def test_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=False))
    assert load_settings(tmp_path).tray_enabled is False


def test_unparseable_file_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{ not json", encoding="utf-8")
    assert load_settings(tmp_path).tray_enabled is True


def test_wrong_shape_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("[]", encoding="utf-8")
    assert load_settings(tmp_path).tray_enabled is True


def test_non_bool_value_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"tray_enabled": "yes"}', encoding="utf-8")
    assert load_settings(tmp_path).tray_enabled is True
