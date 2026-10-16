"""Parity: app_controls.make_controls against the closures it replaced.

tests/fixtures/old_seams.py holds the originals. For the same inputs both must
write the same settings.json and customization.json, leave the same
usage_state, return the same strings and make the same pane calls.
"""
from types import SimpleNamespace

import pytest

from fixtures import old_seams
from tokitty import app_controls, autostart, settings as settings_mod
from tokitty.accounts import Account
from tokitty.app_controls import make_controls
from tokitty.app_core import make_usage_state
from tokitty.customize import Customization, load_customization
from tokitty.settings import load_settings

KINDS = ("old", "new")


class FakePane:
    def __init__(self, index, log):
        self.index = index
        self._label = f"Cat {index}"
        self._log = log

    def set_appearance(self, **kwargs):
        self._log.append(("set_appearance", self.index, kwargs))


class FakeUsage:
    def __init__(self):
        self.rebuilds = 0

    def rebuild_for_window(self):
        self.rebuilds += 1


class FakeDeck:
    def __init__(self, log):
        self._log = log

    def set_hidden_accounts(self, names):
        self._log.append(("hidden", list(names)))


class FakeBackend:
    def __init__(self, registered=False):
        self.registered = registered
        self.calls = []

    def is_registered(self):
        return self.registered

    def deregister(self):
        self.calls.append("deregister")
        self.registered = False


def _read(path):
    return path.read_bytes() if path.exists() else None


def build(kind, tmp_path, monkeypatch, *, panes=2, with_accounts=True, startup=None):
    """One independent world: a state dir, units with fake panes, one seam set."""
    state_dir = tmp_path / kind
    state_dir.mkdir()
    if startup:
        settings_mod.update_settings(state_dir, **startup)
    log = []
    settings = load_settings(state_dir)
    usage_state = make_usage_state(settings)
    units = []
    for i in range(panes):
        account = Account(name=f"acct{i}", config_dir=f"/cfg/{i}") if with_accounts else None
        units.append({
            "key": f"k{i}", "account": account, "tag_kind": None,
            "pane": FakePane(i, log), "usage": FakeUsage(),
        })
    store = {u["key"]: Customization() for u in units}
    deck = FakeDeck(log)
    layout = []
    warns = []

    def apply_customization(pane, custom):
        log.append(("apply", pane.index, custom))

    pane_objs = [u["pane"] for u in units]
    rolls = iter(range(1, 1000))

    def fake_random(colorways, patterns):
        return colorways[next(rolls) % len(colorways)], patterns[next(rolls) % len(patterns)]

    sent = []

    def fake_send(url, topic, token):
        sent.append((url, topic, token))
        return "boom" if topic == "bad" else None

    monkeypatch.setattr(app_controls, "random_look", fake_random)
    monkeypatch.setattr(app_controls, "ntfy_send_test", fake_send)
    monkeypatch.setattr(old_seams, "random_look", fake_random)
    monkeypatch.setattr(old_seams, "ntfy_send_test", fake_send)
    monkeypatch.setattr(settings_mod, "generate_ntfy_topic", lambda: "tokitty-fixed")

    if kind == "old":
        seams = old_seams.make_old_seams(
            state_dir, units, usage_state, store, settings,
            apply_customization=apply_customization, deck=deck, panes=pane_objs,
            warn=lambda title, text: warns.append((title, text)),
        )
    else:
        controls = make_controls(
            state_dir, units, usage_state, store, settings,
            apply_customization=apply_customization,
            on_layout_changed=lambda i, c: layout.append((i, c)),
            distro_probe=SimpleNamespace(get_running=lambda: ["Ubuntu"]),
            deck_fn=lambda: deck,
            label_fn=lambda i: pane_objs[i]._label if i < len(pane_objs) else None,
            warn=lambda title, text: warns.append((title, text)),
        )
        seams = SimpleNamespace(**controls.seams(), autostart_backend=controls.autostart_backend)
    return SimpleNamespace(
        seams=seams, state_dir=state_dir, units=units, usage_state=usage_state,
        store=store, log=log, layout=layout, warns=warns, sent=sent,
    )


def snapshot(world):
    return {
        "settings": _read(world.state_dir / "settings.json"),
        "customization": _read(world.state_dir / "customization.json"),
        "usage_state": world.usage_state,
        "store": world.store,
        "log": world.log,
        "rebuilds": [u["usage"].rebuilds for u in world.units],
        "warns": world.warns,
        "sent": world.sent,
    }


def run_both(tmp_path, monkeypatch, scenario, **build_kwargs):
    out = {}
    for kind in KINDS:
        world = build(kind, tmp_path, monkeypatch, **build_kwargs)
        out[kind] = (scenario(world.seams, world), snapshot(world))
    assert out["old"] == out["new"]
    return out["new"]


def test_customization_fields(tmp_path, monkeypatch):
    steps = [
        (0, "colorway", "black"), (0, "colorway", "nope"), (1, "colorway", None),
        (0, "pattern", "tuxedo"), (0, "pattern", "nope"),
        (0, "coat_base", "#112233"), (0, "coat_shade", "#445566"),
        (0, "card_bg", "#778899"), (0, "bar_fill", "#aabbcc"),
        (1, "coat_base", ""), (1, "coat_base", None),
        (0, "label", "Work"), (0, "label", ""), (1, "label", None),
        (0, "reset", None), (1, "randomize", None), (0, "unknown", "x"),
    ]

    def scenario(seams, world):
        for step in steps:
            seams.on_customization_changed(*step)
        seams.on_randomize(0)
        return [load_customization(world.state_dir)]

    result, snap = run_both(tmp_path, monkeypatch, scenario)
    assert snap["customization"] is not None


def test_customization_with_no_accounts_still_matches(tmp_path, monkeypatch):
    def scenario(seams, world):
        seams.on_customization_changed(0, "label", "Solo")
        seams.on_customization_changed(0, "colorway", "black")

    run_both(tmp_path, monkeypatch, scenario, panes=1, with_accounts=False)


def test_cat_home_saves_and_reports_layout(tmp_path, monkeypatch):
    world = build("new", tmp_path, monkeypatch)
    seams = world.seams
    seams.on_customization_changed(1, "cat_home", "on_top")
    assert world.store["k1"].cat_home == "on_top"
    assert load_customization(world.state_dir)["k1"].cat_home == "on_top"
    assert world.layout == [(1, world.store["k1"])]
    assert ("apply", 1, world.store["k1"]) in world.log
    seams.on_customization_changed(1, "cat_home", "nowhere")
    seams.on_customization_changed(1, "cat_home", None)
    # Like an unknown colorway, a bad spot changes nothing but still saves.
    assert world.store["k1"].cat_home == "on_top"
    assert len(world.layout) == 3


def test_layout_hook_runs_after_every_successful_save(tmp_path, monkeypatch):
    world = build("new", tmp_path, monkeypatch)
    world.seams.on_customization_changed(0, "colorway", "black")
    world.seams.on_customization_changed(0, "unknown", "x")
    world.seams.on_customization_changed(0, "label", "Name")
    assert [i for i, _ in world.layout] == [0, 0]


@pytest.mark.parametrize("startup", [{}, {"surprise_me": True}])
def test_surprise_toggle_on_and_off(tmp_path, monkeypatch, startup):
    def scenario(seams, world):
        seen = [seams.surprise_me()]
        seams.on_toggle_surprise()
        seen.append(seams.surprise_me())
        seams.on_toggle_surprise()
        seen.append(seams.surprise_me())
        return seen

    run_both(tmp_path, monkeypatch, scenario, startup=startup)


def test_view_window_readout(tmp_path, monkeypatch):
    def scenario(seams, world):
        seams.on_view_mode("models")
        seams.on_usage_window("24h")
        seams.on_usage_readout("tokens")
        return seams.view_mode(), seams.usage_window(), seams.usage_readout()

    result, snap = run_both(tmp_path, monkeypatch, scenario)
    assert result[1] == "24h"
    assert snap["rebuilds"] == [1, 1]


@pytest.mark.parametrize("text", ["", "$12.50", "0", "-1", "abc", "inf", "nan"])
def test_budgets(tmp_path, monkeypatch, text):
    def scenario(seams, world):
        first = seams.set_budget_for_pane(1, "$5")
        err = seams.set_budget_for_pane(1, text)
        return first, err, seams.budget_for_pane(1), seams.budget_for_pane(0)

    run_both(tmp_path, monkeypatch, scenario)


def test_budget_follows_the_usage_window(tmp_path, monkeypatch):
    def scenario(seams, world):
        seams.set_budget_for_pane(0, "10")
        seams.on_usage_window("24h")
        return seams.budget_for_pane(0)

    assert run_both(tmp_path, monkeypatch, scenario)[0] is None


@pytest.mark.parametrize("kind", ["session", "weekly"])
@pytest.mark.parametrize("text", ["80", "80%", "0", "101", "abc", ""])
def test_note_thresholds(tmp_path, monkeypatch, kind, text):
    def scenario(seams, world):
        err = seams.set_usage_note_threshold(kind, text)
        return err, seams.usage_note_threshold("session"), seams.usage_note_threshold("weekly")

    run_both(tmp_path, monkeypatch, scenario)


def test_usage_notes_toggle(tmp_path, monkeypatch):
    def scenario(seams, world):
        out = [seams.usage_notes_enabled()]
        seams.on_toggle_usage_notes()
        out.append(seams.usage_notes_enabled())
        seams.on_toggle_usage_notes()
        out.append(seams.usage_notes_enabled())
        return out

    run_both(tmp_path, monkeypatch, scenario)


@pytest.mark.parametrize("field,good,bad", [
    ("url", "https://ntfy.example.com/x", "ftp://nope"),
    ("topic", "my-topic_1", "no spaces"),
    ("token", "tk_secret", "has space"),
])
def test_ntfy_values(tmp_path, monkeypatch, field, good, bad):
    def scenario(seams, world):
        err_bad = seams.set_ntfy_value(field, bad)
        value_after_bad = seams.ntfy_value(field)
        err_good = seams.set_ntfy_value(field, good)
        return err_bad, value_after_bad, err_good, seams.ntfy_value(field), seams.ntfy_enabled()

    result, _ = run_both(tmp_path, monkeypatch, scenario)
    assert result[0] is not None and result[2] is None and result[3] == good


def test_ntfy_toggle_generates_a_topic_once(tmp_path, monkeypatch):
    def scenario(seams, world):
        seams.on_toggle_ntfy()
        on = (seams.ntfy_enabled(), seams.ntfy_value("topic"))
        seams.on_toggle_ntfy()
        off = (seams.ntfy_enabled(), seams.ntfy_value("topic"))
        return on, off

    result, _ = run_both(tmp_path, monkeypatch, scenario)
    assert result == ((True, "tokitty-fixed"), (False, "tokitty-fixed"))


def test_ntfy_toggle_keeps_an_existing_topic(tmp_path, monkeypatch):
    def scenario(seams, world):
        seams.set_ntfy_value("topic", "mine")
        seams.on_toggle_ntfy()
        return seams.ntfy_value("topic")

    assert run_both(tmp_path, monkeypatch, scenario)[0] == "mine"


def test_send_ntfy_test(tmp_path, monkeypatch):
    def scenario(seams, world):
        no_topic = seams.send_ntfy_test()
        seams.set_ntfy_value("topic", "bad")
        failed = seams.send_ntfy_test()
        seams.set_ntfy_value("topic", "good")
        seams.set_ntfy_value("token", "tk")
        return no_topic, failed, seams.send_ntfy_test()

    result, snap = run_both(tmp_path, monkeypatch, scenario)
    assert result[0] == "Enter a topic first."
    assert len(snap["sent"]) == 2


def test_streamdock_account_toggles(tmp_path, monkeypatch):
    def scenario(seams, world):
        rows = seams.streamdock_account_toggles()
        labels = [r[0] for r in rows]
        rows[0][2]()
        shown_after_hide = [r[1]() for r in seams.streamdock_account_toggles()]
        rows[0][2]()
        shown_after_show = [r[1]() for r in seams.streamdock_account_toggles()]
        return labels, shown_after_hide, shown_after_show

    result, snap = run_both(tmp_path, monkeypatch, scenario)
    assert result[0] == ["Cat 0 (/cfg/0)", "Cat 1 (/cfg/1)"]
    assert result[1] == [False, True]
    assert ("hidden", ["acct0"]) in snap["log"]


def test_streamdock_toggles_skip_the_default_account(tmp_path, monkeypatch):
    def scenario(seams, world):
        return seams.streamdock_account_toggles()

    assert run_both(tmp_path, monkeypatch, scenario, panes=1, with_accounts=False)[0] == []


def test_streamdock_toggle_save_failure(tmp_path, monkeypatch, capsys):
    def boom(state_dir, names):
        raise OSError("disk full")

    monkeypatch.setattr("tokitty.streamdock.presets.save_hidden_accounts", boom)

    def scenario(seams, world):
        rows = seams.streamdock_account_toggles()
        rows[0][2]()
        return [r[1]() for r in seams.streamdock_account_toggles()]

    result, snap = run_both(tmp_path, monkeypatch, scenario)
    assert result == [True, True]
    assert not any(entry[0] == "hidden" for entry in snap["log"])
    assert capsys.readouterr().err.count("could not save settings: disk full") == 2


def _patch_autostart(monkeypatch, backend, *, translocated=False, ensure_raises=None):
    monkeypatch.setattr(autostart, "get_backend", lambda: backend)

    def ensure(state_dir, b):
        if ensure_raises is not None:
            raise ensure_raises

    def register(state_dir, b):
        if translocated:
            raise autostart.AppTranslocatedError()
        b.registered = True

    monkeypatch.setattr(autostart, "ensure_current", ensure)
    monkeypatch.setattr(autostart, "write_launcher_and_register", register)


def test_autostart_toggle_on_and_off(tmp_path, monkeypatch):
    def scenario(seams, world):
        out = [seams.autostart_enabled()]
        seams.on_toggle_autostart()
        out.append(seams.autostart_enabled())
        seams.on_toggle_autostart()
        out.append(seams.autostart_enabled())
        return out

    for kind in KINDS:
        backend = FakeBackend()
        _patch_autostart(monkeypatch, backend)
        world = build(kind, tmp_path, monkeypatch)
        assert scenario(world.seams, world) == [False, True, False]
        assert backend.calls == ["deregister"]


def test_autostart_translocated_warns_and_reflects_the_backend(tmp_path, monkeypatch):
    results = {}
    for kind in KINDS:
        backend = FakeBackend()
        _patch_autostart(monkeypatch, backend, translocated=True)
        world = build(kind, tmp_path, monkeypatch)
        world.seams.on_toggle_autostart()
        results[kind] = (world.seams.autostart_enabled(), world.warns)
    assert results["old"] == results["new"]
    enabled, warns = results["new"]
    assert enabled is False
    assert [w[0] for w in warns] == ["Start at login"] and warns[0][1]


@pytest.mark.parametrize("backend", [None, "oserror", "importerror"])
def test_autostart_unavailable_leaves_the_seams_none(tmp_path, monkeypatch, backend):
    for kind in KINDS:
        if backend is None:
            _patch_autostart(monkeypatch, None)
        else:
            exc = OSError("denied") if backend == "oserror" else ImportError("winreg")
            _patch_autostart(monkeypatch, FakeBackend(), ensure_raises=exc)
        world = build(kind, tmp_path, monkeypatch)
        assert world.seams.autostart_enabled is None
        assert world.seams.on_toggle_autostart is None


def test_autostart_backend_is_exposed_for_the_update_host(tmp_path, monkeypatch):
    backend = FakeBackend()
    _patch_autostart(monkeypatch, backend)
    assert build("new", tmp_path, monkeypatch).seams.autostart_backend is backend


def test_running_distros_is_the_probe(tmp_path, monkeypatch):
    assert build("new", tmp_path, monkeypatch).seams.running_distros() == ["Ubuntu"]


def test_seams_leave_out_the_backend():
    fields = app_controls.Controls.__dataclass_fields__
    assert "autostart_backend" in fields
    stub = {name: None for name in fields}
    assert "autostart_backend" not in app_controls.Controls(**stub).seams()


def test_tail_saves_validates_and_restyles(tmp_path, monkeypatch):
    world = build("new", tmp_path, monkeypatch)
    seams = world.seams
    seams.on_customization_changed(1, "tail", "ringed_shade")
    assert world.store["k1"].tail == "ringed_shade"
    assert load_customization(world.state_dir)["k1"].tail == "ringed_shade"
    assert ("apply", 1, world.store["k1"]) in world.log
    seams.on_customization_changed(1, "tail", "nope")
    seams.on_customization_changed(1, "tail", None)
    assert world.store["k1"].tail == "ringed_shade"


def test_randomize_and_reset_keep_the_tail(tmp_path, monkeypatch):
    # Reset to preset clears the custom colours only; like colorway and
    # pattern, the tail is a look choice it leaves alone.
    world = build("new", tmp_path, monkeypatch)
    seams = world.seams
    seams.on_customization_changed(0, "tail", "ringed")
    seams.on_customization_changed(0, "coat_base", "#112233")
    seams.on_randomize(0)
    assert world.store["k0"].tail == "ringed"
    seams.on_customization_changed(0, "reset", None)
    assert world.store["k0"].tail == "ringed"
    assert world.store["k0"].overrides == {}
