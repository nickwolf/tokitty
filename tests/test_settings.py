from tokitty.settings import Settings, load_settings, save_settings, update_settings


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


def test_surprise_me_default_false(tmp_path):
    assert load_settings(tmp_path).surprise_me is False


def test_surprise_me_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=True, surprise_me=True))
    assert load_settings(tmp_path).surprise_me is True


def test_surprise_me_non_bool_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"surprise_me": "yes"}', encoding="utf-8")
    assert load_settings(tmp_path).surprise_me is False


def test_update_leaves_other_fields_alone(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=True, surprise_me=True))
    update_settings(tmp_path, tray_enabled=False)
    loaded = load_settings(tmp_path)
    assert loaded.tray_enabled is False
    assert loaded.surprise_me is True


def test_update_returns_the_saved_settings(tmp_path):
    assert update_settings(tmp_path, surprise_me=True) == load_settings(tmp_path)


def test_update_on_a_missing_file_starts_from_defaults(tmp_path):
    update_settings(tmp_path, surprise_me=True)
    loaded = load_settings(tmp_path)
    assert loaded.surprise_me is True
    assert loaded.tray_enabled is True
