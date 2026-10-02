import json

import pytest

from tokitty.settings import load_settings, save_settings, Settings
from tokitty.streamdock import install
from tokitty.streamdock.pending import ENABLED_MARKER, touch_enabled

TOKEN = "A" * 43


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "src" / install.PLUGIN_FOLDER
    src.mkdir(parents=True)
    for name in ("manifest.json", "index.html", "pi.html", "icon.png"):
        (src / name).write_text(name)
    appdata = tmp_path / "appdata"
    plugins = appdata / "HotSpot" / "StreamDock" / "plugins"
    plugins.mkdir(parents=True)
    return {
        "src": src,
        "appdata": appdata,
        "plugins": plugins,
        "state": tmp_path / "state",
        "target": plugins / install.PLUGIN_FOLDER,
        "tmp": tmp_path,
    }


def run_install(env, out, **kw):
    kw.setdefault("port_fn", lambda: 40123)
    kw.setdefault("token_fn", lambda: TOKEN)
    return install.install_streamdock(
        state_dir=env["state"], appdata=env["appdata"], plugin_src=env["src"], print_fn=out.append, **kw
    )


def config(env):
    text = (env["target"] / "config.js").read_text(encoding="utf-8")
    prefix, suffix = "window.TOKITTY = ", ";\n"
    assert text.startswith(prefix) and text.endswith(suffix)
    return json.loads(text[len(prefix):-len(suffix)])


def test_missing_vsd_craft_is_exit_1_and_writes_nothing(tmp_path):
    out = []
    code = install.install_streamdock(
        state_dir=tmp_path / "state", appdata=tmp_path / "none", plugin_src=tmp_path, print_fn=out.append
    )
    assert code == 1
    assert "VSD Craft not found" in out[0]
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "none").exists()


def test_install_writes_plugin_and_config(env):
    out = []
    assert run_install(env, out) == 0
    assert sorted(p.name for p in env["target"].iterdir()) == [
        "config.js", "icon.png", "index.html", "manifest.json", "pi.html"]
    assert config(env) == {"port": 40123, "token": TOKEN}
    s = load_settings(env["state"])
    assert (s.streamdock_port, s.streamdock_token) == (40123, TOKEN)
    assert any("exit VSD Craft" in line for line in out)


def test_token_is_never_printed(env):
    out = []
    run_install(env, out)
    run_install(env, out)
    assert not any(TOKEN in line for line in out)


def test_second_install_is_idempotent_and_removes_stale_files(env):
    run_install(env, [])
    (env["target"] / "stale.txt").write_text("old")
    ports = iter([1111, 2222])
    code = run_install(env, [], port_fn=lambda: next(ports), token_fn=lambda: "B" * 43)
    assert code == 0
    assert config(env) == {"port": 40123, "token": TOKEN}
    assert not (env["target"] / "stale.txt").exists()
    assert sorted(p.name for p in env["target"].iterdir()) == [
        "config.js", "icon.png", "index.html", "manifest.json", "pi.html"]


def test_invalid_stored_values_are_replaced(env):
    env["state"].mkdir()
    (env["state"] / "settings.json").write_text(json.dumps({"streamdock_port": 80, "streamdock_token": "short"}))
    assert run_install(env, []) == 0
    assert config(env) == {"port": 40123, "token": TOKEN}


def test_a_stored_port_with_no_token_picks_both_anew(env):
    env["state"].mkdir()
    save_settings(env["state"], Settings(streamdock_port=5000))
    assert run_install(env, []) == 0
    assert config(env)["token"] == TOKEN


def test_default_plugin_source_is_the_bundled_folder():
    src = install.default_plugin_src()
    assert (src / "manifest.json").is_file() and (src / "index.html").is_file()


def test_free_port_is_usable():
    port = install._free_port()
    assert 1024 <= port <= 65535


def test_uninstall_cleans_up(env):
    run_install(env, [])
    dirs = [env["tmp"] / "a" / "tokitty", env["tmp"] / "b" / "tokitty"]
    for d in dirs[:1]:
        touch_enabled(d)
    assert (dirs[0] / ENABLED_MARKER).exists()
    out = []
    code = install.uninstall_streamdock(
        state_dir=env["state"], appdata=env["appdata"], tokitty_dirs=dirs, print_fn=out.append
    )
    assert code == 0
    assert not env["target"].exists()
    assert env["plugins"].is_dir()
    s = load_settings(env["state"])
    assert (s.streamdock_port, s.streamdock_token) == (0, "")
    assert not (dirs[0] / ENABLED_MARKER).exists()
    assert not dirs[1].exists()
    assert not any(TOKEN in line for line in out)


def test_uninstall_is_idempotent_and_creates_nothing(env):
    for _ in range(2):
        assert install.uninstall_streamdock(
            state_dir=env["state"], appdata=env["appdata"], tokitty_dirs=[], print_fn=lambda *_: None
        ) == 0
    assert not env["state"].exists()


def test_settings_validation_of_port_and_token(tmp_path):
    def load(**data):
        (tmp_path / "settings.json").write_text(json.dumps(data))
        return load_settings(tmp_path)

    assert load(streamdock_port=40000).streamdock_port == 40000
    for bad in (80, 1023, 65536, True, "9000", 9000.5, None):
        assert load(streamdock_port=bad).streamdock_port == 0
    good = "a-Z_9" * 7
    assert load(streamdock_token=good).streamdock_token == good
    for bad in ("x" * 31, "x" * 129, "a b" * 20, "é" * 40, 5, None, "a" * 31 + "!"):
        assert load(streamdock_token=bad).streamdock_token == ""
    assert Settings().streamdock_port == 0 and Settings().streamdock_token == ""
