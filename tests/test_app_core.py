"""Direct tests for tokitty.app_core, the toolkit-free half of the app."""
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.accounts import Account
from tokitty.credentials import CredentialsError
from tokitty.activity import ActivityView
from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.behaviour import drives
from tokitty.app_core import (
    PaneUpdate, _limits_hover_for, build_units, make_onboarder, rebuild_units, same_accounts, step_unit,
)
from tokitty.burn import PACE_CAPS, PACE_UNKNOWN, PACE_WONT_CAP, BurnTracker, Pace
from tokitty.customize import Customization, load_customization, save_customization
from tokitty.display import format_reset_day, format_reset_time
from tokitty.hover import LimitHover, LimitsHover
from tokitty.poller import PollResult
from tokitty.providers import get_provider
from tokitty.startup import ONBOARDING_MODELS_AUTOSELECT

NOW = datetime(2030, 1, 2, 9, 0, tzinfo=timezone.utc)
RESETS = datetime(2030, 1, 2, 15, 0, tzinfo=timezone.utc)
WEEKLY_RESETS = datetime(2030, 1, 6, 15, 0, tzinfo=timezone.utc)


def test_import_isolation():
    code = (
        "import sys\n"
        "sys.modules['tkinter'] = None\n"
        "sys.modules['PySide6'] = None\n"
        "import tokitty.app_core\n"
        "assert 'tokitty.__main__' not in sys.modules\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


class _Worker:
    def __init__(self, latest):
        self.latest = latest

    def get_latest(self):
        return self.latest


class _Notifier:
    def __init__(self):
        self.calls = []

    def check(self, key, label, alerts, cfg):
        self.calls.append((key, label, alerts, cfg))


def _snapshot(session_pct=40.0, weekly_pct=20.0):
    return UsageSnapshot(
        session_pct=session_pct, session_resets_at=RESETS,
        weekly_pct=weekly_pct, weekly_resets_at=WEEKLY_RESETS, limits=[], fetched_at=NOW,
    )


def _poll(status="ok", snapshot=None, fetched_at=NOW):
    return PollResult(status=status, snapshot=snapshot, message=None, fetched_at=fetched_at)


def _breakdown():
    from tokitty.usage_scan import ModelUsage, UsageBreakdown

    return UsageBreakdown(
        status="ok", window="7d", window_start=NOW - timedelta(days=7),
        models=(ModelUsage("claude-opus-5", 100, 50, 0, 0, 0, 3.0),),
        total_cost_usd=3.0, total_tokens=150, scanned_at=NOW,
    )


def _unit(poll=None, breakdown=None, activity=None, last_good=None):
    """A fresh unit per call, so no two tests (or two units) share a tracker."""
    return {
        "poller": _Worker(poll), "usage": _Worker(breakdown), "watcher": _Worker(activity),
        "provider": get_provider(None), "key": "default", "last_good": last_good,
        "burn": BurnTracker(), "sessions_dir": "/home/x/.claude/tokitty/sessions",
    }


def _usage_state(**overrides):
    state = {
        "view": "limits", "window": "7d", "readout": "cost", "budgets": {},
        "onboarding": ONBOARDING_MODELS_AUTOSELECT, "notes": (False, 90, 95),
        "ntfy": (False, "https://ntfy.sh", "", ""),
    }
    state.update(overrides)
    return state


@pytest.fixture
def events(monkeypatch):
    from tokitty import usage_notes

    log = []
    monkeypatch.setattr(
        usage_notes, "write_usage_file",
        lambda tokitty_dir, fetched_at, alerts: log.append(("note_write", tokitty_dir, fetched_at, alerts)),
    )
    monkeypatch.setattr(usage_notes, "remove_usage_file", lambda tokitty_dir: log.append(("note_remove", tokitty_dir)))
    return log


def _step(unit, events, *, usage_state=None, notifier=None, store=None, removed=None, onboard=None,
          **extra):
    return step_unit(
        unit,
        usage_state=usage_state or _usage_state(),
        notifier=notifier or _Notifier(),
        customization_store=store if store is not None else {},
        notes_removed=removed if removed is not None else set(),
        onboard=onboard or (lambda unit, latest, breakdown: events.append(("onboard", latest, breakdown))),
        now=NOW,
        **extra,
    )


def test_step_unit_shows_a_codex_balance_on_the_card(events):
    snapshot = replace(_snapshot(), credits_balance=12.4)
    update = _step(_unit(poll=_poll(snapshot=snapshot)), events, usage_state=_usage_state(notes=(True, 90, 95)))
    assert update.kwargs["credits_text"] == "$12.40 left"


def test_step_unit_limits_ok(events):
    poll = _poll(snapshot=_snapshot())
    unit = _unit(poll=poll)

    update = _step(unit, events, usage_state=_usage_state(notes=(True, 90, 95)))

    assert update == PaneUpdate("limits", {
        "state": "content",
        "session_pct": 40.0,
        "weekly_pct": 20.0,
        "session_reset_text": format_reset_time(RESETS),
        "weekly_reset_text": format_reset_day(WEEKLY_RESETS),
        "driving_tag": "5h",
        "credits_text": None,
        "stale_text": None,
        "hint_text": None,
        "dimmed": False,
        "projection_text": None,
        "hover": LimitsHover(
            session=LimitHover("Session", "session", 40.0, RESETS, False, Pace(PACE_UNKNOWN)),
            weekly=LimitHover("Week", "weekly", 20.0, WEEKLY_RESETS, False, Pace(PACE_UNKNOWN)),
        ),
        "tool_label": "",
        "accent": False,
        "drives": drives(None),
        "bar_events": (0, 0, 0),
    })
    assert [e[0] for e in events] == ["onboard", "note_write"]
    assert events[0][1:] == (poll, None)
    assert events[1][1:] == ("/home/x/.claude/tokitty", NOW, [])
    assert unit["last_good"] is poll
    assert [s.fetched_at for s in unit["burn"].samples] == [NOW]
    assert unit["deck_usage"] == (40.0, 20.0, False)
    assert unit["tray_usage"] == (40.0, 20.0, "resets " + format_reset_time(RESETS))


def test_step_unit_models_view(events):
    from tokitty.usage_display import build_view

    poll = _poll(snapshot=_snapshot(session_pct=10.0, weekly_pct=5.0))
    breakdown = _breakdown()
    unit = _unit(poll=poll, breakdown=breakdown)

    update = _step(unit, events, usage_state=_usage_state(view="models"))

    assert update == PaneUpdate("models", {
        "state": "content",
        "usage_view": build_view(breakdown, "cost", None),
        "driving_tag": "",
        "tool_label": "",
        "accent": False,
        "drives": drives(None),
    })
    assert unit["last_good"] is poll
    assert len(unit["burn"].samples) == 1
    assert unit["deck_usage"] == (10.0, 5.0, False)


def test_step_unit_models_view_hides_a_failed_poll(events):
    unit = _unit(poll=_poll(status="stale_token"), breakdown=_breakdown())

    update = _step(unit, events, usage_state=_usage_state(view="models"))

    assert update.kind == "models"
    assert update.kwargs["state"] == "content"
    assert update.kwargs["driving_tag"] == ""


def test_step_unit_stale_token_without_history(events):
    unit = _unit(poll=_poll(status="stale_token"))

    update = _step(unit, events)

    assert update == PaneUpdate("limits", {
        "state": "confused",
        "session_pct": 0.0,
        "weekly_pct": 0.0,
        "session_reset_text": "—",
        "weekly_reset_text": "—",
        "driving_tag": "",
        "credits_text": None,
        "hint_text": "token stale, open Claude Code",
        "dimmed": True,
        "stale_text": None,
        "projection_text": None,
        "hover": None,
        "tool_label": "",
        "accent": False,
        "drives": drives(None),
        "bar_events": (0, 0, 0),
    })
    assert unit["last_good"] is None
    assert unit["burn"].samples == []
    assert unit["deck_usage"] == (0.0, 0.0, True)
    assert unit["tray_usage"] is None


def test_step_unit_stale_token_with_a_last_good_sleeps(events):
    seen = datetime(2030, 1, 2, 7, 30, tzinfo=timezone.utc)
    last_good = _poll(snapshot=_snapshot(session_pct=55.0, weekly_pct=30.0), fetched_at=seen)
    unit = _unit(poll=_poll(status="stale_token"), last_good=last_good)

    update = _step(unit, events)

    assert update.kwargs["state"] == "sleeping"
    assert update.kwargs["session_pct"] == 55.0
    assert update.kwargs["weekly_pct"] == 30.0
    assert update.kwargs["hint_text"] == "last seen " + seen.astimezone().strftime("%H:%M")
    assert update.kwargs["dimmed"] is True
    assert unit["last_good"] is last_good
    assert unit["burn"].samples == []
    assert unit["deck_usage"] == (55.0, 30.0, True)
    assert unit["tray_usage"][:2] == (55.0, 30.0)


def test_step_unit_api_error_with_a_last_good_keeps_the_numbers(events):
    last_good = _poll(snapshot=_snapshot(session_pct=55.0, weekly_pct=30.0))
    unit = _unit(poll=_poll(status="api_error"), last_good=last_good)

    update = _step(unit, events)

    assert update.kwargs["state"] == "interested"
    assert update.kwargs["session_pct"] == 55.0
    assert update.kwargs["hint_text"] is None
    assert update.kwargs["dimmed"] is False
    assert unit["last_good"] is last_good
    assert unit["deck_usage"] == (55.0, 30.0, False)


def test_step_unit_accented_pose(events):
    unit = _unit(poll=_poll(snapshot=_snapshot()), activity=ActivityView(state="permission", tool_label="Bash"))

    update = _step(unit, events)

    assert update.kwargs["state"] == "permission"
    assert update.kwargs["tool_label"] == ""
    assert update.kwargs["accent"] is True


def test_step_unit_working_pose_carries_the_tool_label(events):
    unit = _unit(poll=_poll(snapshot=_snapshot()), activity=ActivityView(state="working", tool_label="Edit"))

    update = _step(unit, events)

    assert update.kwargs["state"] == "working"
    assert update.kwargs["tool_label"] == "Edit"
    assert update.kwargs["accent"] is False


def test_step_unit_without_a_poll_result_returns_none_after_the_side_effects(events):
    unit = _unit(poll=None)

    update = _step(unit, events, removed=set())

    assert update is None
    assert [e[0] for e in events] == ["onboard", "note_remove"]
    assert unit["last_good"] is None
    assert unit["burn"].samples == []
    assert "deck_usage" not in unit


def test_step_unit_runs_onboarding_notes_then_ntfy(events):
    poll = _poll(snapshot=_snapshot(session_pct=95.0, weekly_pct=20.0))
    unit = _unit(poll=poll)
    cfg = ("https://ntfy.sh", "cats", "tok")
    state = _usage_state(notes=(True, 90, 95), ntfy=(True, *cfg))

    class _Order(_Notifier):
        def check(self, key, label, alerts, cfg):
            events.append(("ntfy",))
            super().check(key, label, alerts, cfg)

    notifier = _Order()
    update = _step(unit, events, usage_state=state, notifier=notifier)

    assert update.kind == "limits"
    assert [e[0] for e in events] == ["onboard", "note_write", "ntfy"]
    alerts = events[1][3]
    assert [a["kind"] for a in alerts] == ["session"]
    assert notifier.calls == [("default", "Claude", alerts, cfg)]


def test_step_unit_uses_a_stored_label_for_ntfy(events):
    poll = _poll(snapshot=_snapshot(session_pct=95.0))
    unit = _unit(poll=poll)
    notifier = _Notifier()

    _step(unit, events, usage_state=_usage_state(ntfy=(True, "u", "t", "")), notifier=notifier,
          store={"default": Customization(colorway="gray", pattern="solid", label=" Work ")})

    assert notifier.calls[0][1] == "Work"


def test_two_units_keep_independent_state(events):
    first = _unit(poll=_poll(snapshot=_snapshot(session_pct=10.0)))
    second = _unit(poll=_poll(status="api_error"))

    _step(first, events)
    _step(second, events)

    assert first["last_good"] is not None and second["last_good"] is None
    assert len(first["burn"].samples) == 1 and second["burn"].samples == []


def test_onboarder_switches_an_api_key_user_to_models_once(tmp_path):
    from tokitty.settings import load_settings

    state = _usage_state(onboarding=0)
    changed = []
    onboard = make_onboarder(tmp_path, state, lambda: changed.append(1))
    unit = _unit()

    onboard(unit, _poll(status="credentials_unreachable"), _breakdown())
    onboard(unit, _poll(status="credentials_unreachable"), _breakdown())

    assert changed == [1]
    assert state["view"] == "models"
    assert state["onboarding"] == ONBOARDING_MODELS_AUTOSELECT
    saved = load_settings(tmp_path)
    assert saved.view_mode == "models"
    assert saved.onboarding_version == ONBOARDING_MODELS_AUTOSELECT


def test_onboarder_leaves_a_subscriber_alone(tmp_path):
    state = _usage_state(onboarding=0)
    changed = []
    onboard = make_onboarder(tmp_path, state, lambda: changed.append(1))

    onboard(_unit(), _poll(snapshot=_snapshot()), _breakdown())
    onboard(_unit(), None, None)

    assert changed == []
    assert state["view"] == "limits"
    assert not (tmp_path / "settings.json").exists()


class _Probe:
    def get_running(self):
        return []


class _NoCredentials:
    # WslCredentialsCache's contract with no WSL install found. Windows
    # resolvers call single(), which raises rather than returning nothing.
    def all_matches(self):
        return []

    def single(self):
        raise CredentialsError("no WSL credentials")

    def claude_dirs(self):
        return []


def _build(tmp_path, accounts, store=None):
    store = {} if store is None else store
    units = build_units(
        accounts=accounts, state_dir=tmp_path, customization_store=store,
        wsl_credentials=_NoCredentials(), distro_probe=_Probe(), usage_state=_usage_state(),
    )
    return units, store


_UNIT_KEYS = {
    "poller", "watcher", "provider", "tag_kind", "last_good", "key", "account", "cred_loader",
    "burn", "usage", "custom", "label", "sessions_dir", "distro_name",
}


def test_build_units_without_accounts_makes_one_default_unit(tmp_path):
    units, store = _build(tmp_path, [])

    assert len(units) == 1
    unit = units[0]
    assert set(unit) == _UNIT_KEYS
    assert "pane" not in unit
    assert unit["key"] == "default"
    assert unit["account"] is None
    assert unit["tag_kind"] is None
    assert unit["label"] == ""
    assert unit["last_good"] is None
    assert unit["provider"].kind == "claude"
    assert store == {"default": unit["custom"]}
    assert load_customization(tmp_path) == {"default": unit["custom"]}


def test_build_units_keeps_a_stored_look_and_label(tmp_path):
    stored = Customization(colorway="gray", pattern="solid", label="Mine")

    units, store = _build(tmp_path, [], store={"default": stored})

    assert units[0]["custom"] == stored
    assert units[0]["label"] == "Mine"
    assert load_customization(tmp_path)["default"] == stored


def test_build_units_one_account(tmp_path):
    account = Account(name="acct-v1-a", config_dir=str(tmp_path / "a"))

    units, store = _build(tmp_path, [account])

    assert [u["key"] for u in units] == ["acct-v1-a"]
    assert units[0]["account"] is account
    assert units[0]["sessions_dir"] == str(tmp_path / "a" / "tokitty" / "sessions")
    assert set(load_customization(tmp_path)) == {"acct-v1-a"}


def test_build_units_two_providers_tag_each_label(tmp_path):
    save_customization(tmp_path, {
        "acct-v1-a": Customization(colorway="gray", pattern="solid", label="Work"),
    })
    accounts = [
        Account(name="acct-v1-a", config_dir=str(tmp_path / "a")),
        Account(name="acct-v1-b", config_dir=str(tmp_path / "b"), provider="codex"),
    ]

    units, _ = _build(tmp_path, accounts, store=load_customization(tmp_path))

    assert [u["tag_kind"] for u in units] == ["claude", "codex"]
    assert [u["provider"].kind for u in units] == ["claude", "codex"]
    assert units[0]["label"] == "claude\u00b7Work"
    assert units[1]["label"] == "codex"
    assert [u["custom"] for u in units] == [load_customization(tmp_path)[k] for k in ("acct-v1-a", "acct-v1-b")]
    saved = load_customization(tmp_path)
    assert set(saved) == {"acct-v1-a", "acct-v1-b"}
    assert saved["acct-v1-a"].label == "Work"


def test_build_units_gives_each_unit_its_own_workers(tmp_path):
    accounts = [
        Account(name="acct-v1-a", config_dir=str(tmp_path / "a")),
        Account(name="acct-v1-b", config_dir=str(tmp_path / "b")),
    ]

    units, _ = _build(tmp_path, accounts)

    for key in ("poller", "watcher", "usage", "cred_loader", "burn"):
        assert units[0][key] is not units[1][key]


def test_build_units_names_each_units_threads_by_index(tmp_path):
    accounts = [
        Account(name="acct-v1-a", config_dir=str(tmp_path / "a")),
        Account(name="acct-v1-b", config_dir=str(tmp_path / "b")),
    ]

    units, _ = _build(tmp_path, accounts)

    for index, unit in enumerate(units):
        assert unit["poller"]._name == f"poller-{index}"
        assert unit["watcher"]._name == f"activity-{index}"
        assert unit["usage"]._name == f"usage-{index}"


def _rebuild(tmp_path, units, accounts, store):
    return rebuild_units(
        units, accounts=accounts, state_dir=tmp_path, customization_store=store,
        wsl_credentials=_NoCredentials(), distro_probe=_Probe(), usage_state=_usage_state(),
    )


def _accounts(tmp_path, *names, codex=()):
    return [Account(name=f"acct-v1-{n}", config_dir=str(tmp_path / n),
                    provider="codex" if n in codex else "claude") for n in names]


def test_rebuild_units_adds_a_unit_and_reuses_the_rest(tmp_path):
    a, b = _accounts(tmp_path, "a", "b")
    units, store = _build(tmp_path, [a])
    old = units[0]
    poller, burn = old["poller"], old["burn"]
    old["last_good"] = "kept"

    rebuilt = _rebuild(tmp_path, units, [a, b], store)

    assert [u["key"] for u in rebuilt.units] == ["acct-v1-a", "acct-v1-b"]
    assert rebuilt.units[0] is old
    assert old["poller"] is poller and old["burn"] is burn and old["last_good"] == "kept"
    assert rebuilt.added == [rebuilt.units[1]] and rebuilt.removed == []
    assert rebuilt.units[1]["poller"] is not poller
    assert set(rebuilt.units[1]) == _UNIT_KEYS


def test_rebuild_units_hands_back_a_removed_unit_without_stopping_it(tmp_path):
    a, b = _accounts(tmp_path, "a", "b")
    units, store = _build(tmp_path, [a, b])
    first, second = units

    rebuilt = _rebuild(tmp_path, units, [b], store)

    assert rebuilt.units == [second] and rebuilt.units[0] is second
    assert rebuilt.added == [] and rebuilt.removed == [first]


def test_rebuild_units_reorders_without_new_units_and_labels_follow(tmp_path):
    save_customization(tmp_path, {
        "acct-v1-a": Customization(colorway="gray", pattern="solid", label="Work"),
        "acct-v1-b": Customization(colorway="gray", pattern="solid", label="Home"),
    })
    a, b = _accounts(tmp_path, "a", "b")
    units, store = _build(tmp_path, [a, b], store=load_customization(tmp_path))
    first, second = units

    rebuilt = _rebuild(tmp_path, units, [b, a], store)

    assert rebuilt.units[0] is second and rebuilt.units[1] is first
    assert rebuilt.added == [] and rebuilt.removed == []
    assert [u["label"] for u in rebuilt.units] == ["Home", "Work"]


def test_rebuild_units_retags_every_unit_when_a_codex_account_arrives(tmp_path):
    save_customization(tmp_path, {
        "acct-v1-a": Customization(colorway="gray", pattern="solid", label="Work"),
    })
    a, b = _accounts(tmp_path, "a", "b", codex=("b",))
    units, store = _build(tmp_path, [a], store=load_customization(tmp_path))
    assert units[0]["tag_kind"] is None and units[0]["label"] == "Work"

    rebuilt = _rebuild(tmp_path, units, [a, b], store)

    assert [u["tag_kind"] for u in rebuilt.units] == ["claude", "codex"]
    assert rebuilt.units[0]["label"] == "claude\u00b7Work"
    assert rebuilt.units[1]["label"] == "codex"

    # And back: with the Codex account gone the tag goes too.
    again = _rebuild(tmp_path, rebuilt.units, [a], store)
    assert again.units[0]["tag_kind"] is None and again.units[0]["label"] == "Work"


def test_rebuild_units_takes_the_look_from_the_file_as_it_is_now(tmp_path):
    a, b = _accounts(tmp_path, "a", "b")
    units, store = _build(tmp_path, [a])
    on_disk = load_customization(tmp_path)
    # The page renamed a and wrote b's look before it said so.
    on_disk["acct-v1-a"] = replace(on_disk["acct-v1-a"], label="Renamed")
    on_disk["acct-v1-b"] = Customization(colorway="gray", pattern="solid", label="New")
    save_customization(tmp_path, on_disk)

    rebuilt = _rebuild(tmp_path, units, [a, b], store)

    assert [u["label"] for u in rebuilt.units] == ["Renamed", "New"]
    assert store["acct-v1-a"].label == "Renamed" and store["acct-v1-b"].label == "New"
    assert [u["custom"] for u in rebuilt.units] == [store["acct-v1-a"], store["acct-v1-b"]]


def test_rebuild_units_saves_only_the_new_look(tmp_path):
    a, b = _accounts(tmp_path, "a", "b")
    units, store = _build(tmp_path, [a])
    # Stale in memory: the file has a newer label for a.
    on_disk = load_customization(tmp_path)
    save_customization(tmp_path, {"acct-v1-a": replace(on_disk["acct-v1-a"], label="Newer")})
    store["acct-v1-x"] = Customization(label="stale only in memory")

    rebuilt = _rebuild(tmp_path, units, [a, b], store)

    saved = load_customization(tmp_path)
    assert set(saved) == {"acct-v1-a", "acct-v1-b"}
    assert saved["acct-v1-a"].label == "Newer"
    assert saved["acct-v1-b"] == rebuilt.units[1]["custom"]


def test_rebuild_units_rebuilds_a_unit_whose_home_changed(tmp_path):
    a = Account(name="acct-v1-a", config_dir=str(tmp_path / "a"))
    moved = Account(name="acct-v1-a", config_dir=str(tmp_path / "elsewhere"))
    units, store = _build(tmp_path, [a])

    rebuilt = _rebuild(tmp_path, units, [moved], store)

    assert rebuilt.removed == units and rebuilt.added == rebuilt.units
    assert rebuilt.units[0]["sessions_dir"] == str(tmp_path / "elsewhere" / "tokitty" / "sessions")


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX spelling of the same dir")
def test_rebuild_units_keeps_a_unit_whose_home_is_only_respelled(tmp_path):
    a = Account(name="acct-v1-a", config_dir=str(tmp_path / "a"))
    respelled = Account(name="acct-v1-a", config_dir=str(tmp_path / "x" / ".." / "a"))
    units, store = _build(tmp_path, [a])

    assert same_accounts(units, [respelled])
    rebuilt = _rebuild(tmp_path, units, [respelled], store)
    assert rebuilt.units == units and rebuilt.units[0]["account"] is respelled


def test_rebuild_units_without_accounts_is_the_default_unit_a_launch_builds(tmp_path):
    a, b = _accounts(tmp_path, "a", "b")
    units, store = _build(tmp_path, [a, b])
    (tmp_path / "fresh").mkdir()
    fresh, _ = _build(tmp_path / "fresh", [])

    rebuilt = _rebuild(tmp_path, units, None, store)

    assert rebuilt.removed == units and rebuilt.added == rebuilt.units
    unit = rebuilt.units[0]
    assert set(unit) == set(fresh[0]) == _UNIT_KEYS
    for key in ("key", "account", "tag_kind", "sessions_dir", "distro_name", "last_good"):
        assert unit[key] == fresh[0][key]
    assert unit["provider"].kind == fresh[0]["provider"].kind == "claude"


def test_rebuild_units_matches_duplicate_accounts_one_for_one(tmp_path):
    (a,) = _accounts(tmp_path, "a")
    units, store = _build(tmp_path, [a, a])

    rebuilt = _rebuild(tmp_path, units, [a], store)

    assert rebuilt.units == [units[0]] and rebuilt.removed == [units[1]]
    assert rebuilt.units[0] is units[0] and rebuilt.removed[0] is units[1]


def test_rebuild_units_that_fails_leaves_the_running_units_alone(tmp_path, monkeypatch):
    from tokitty import app_core

    save_customization(tmp_path, {
        "acct-v1-a": Customization(colorway="gray", pattern="solid", label="Work"),
    })
    a, b = _accounts(tmp_path, "a", "b", codex=("b",))
    units, store = _build(tmp_path, [a], store=load_customization(tmp_path))
    units[0]["pane"] = "pane 0"
    before = dict(units[0])

    def boom(*args, **kwargs):
        raise OSError("unreachable home")

    monkeypatch.setattr(app_core, "build_unit", boom)
    with pytest.raises(OSError):
        _rebuild(tmp_path, units, [a, b], store)

    assert units[0] == before and units[0]["label"] == "Work" and units[0]["tag_kind"] is None


def test_same_accounts_compares_identity_and_order(tmp_path):
    a, b = _accounts(tmp_path, "a", "b")
    units, _ = _build(tmp_path, [a, b])

    assert same_accounts(units, [a, b])
    assert not same_accounts(units, [b, a])
    assert not same_accounts(units, [a])
    assert not same_accounts(units, None)
    default, _ = _build(tmp_path, [])
    assert same_accounts(default, None) and same_accounts(default, [])


_MEXICO = timezone(timedelta(hours=-6))


@pytest.mark.parametrize("view", ["limits", "models"])
def test_step_unit_drives_follow_the_hour_in_the_given_zone(events, view):
    # 09:00 UTC is 03:00 in UTC-6: night there, morning in UTC
    usage_state = _usage_state(view=view)
    unit = _unit(poll=_poll(snapshot=_snapshot()), breakdown=_breakdown())
    night = _step(unit, events, usage_state=usage_state, time_of_day=True, tz=_MEXICO)
    assert night.kwargs["drives"] == drives(3)
    morning = _step(unit, events, usage_state=usage_state, time_of_day=True, tz=timezone.utc)
    assert morning.kwargs["drives"] == drives(9)
    assert night.kwargs["drives"] != morning.kwargs["drives"]


@pytest.mark.parametrize("view", ["limits", "models"])
def test_step_unit_without_time_of_day_has_plain_drives(events, view):
    unit = _unit(poll=_poll(snapshot=_snapshot()), breakdown=_breakdown())
    update = _step(unit, events, usage_state=_usage_state(view=view), tz=_MEXICO)
    assert update.kwargs["drives"] == drives(None)


@pytest.mark.parametrize("view", ["limits", "models"])
def test_step_unit_without_wander_never_plays(events, view):
    unit = _unit(poll=_poll(snapshot=_snapshot()), breakdown=_breakdown())
    update = _step(unit, events, usage_state=_usage_state(view=view), time_of_day=True,
                   tz=_MEXICO, wander=False)
    assert update.kwargs["drives"] == replace(drives(3), play=0.0, bat=0.0, pounce=0.0)


def test_step_unit_drives_follow_the_stores_personality(events):

    unit = _unit(poll=_poll(snapshot=_snapshot()))
    store = {"default": Customization(personality="lazy")}
    update = _step(unit, events, store=store, time_of_day=True, tz=timezone.utc)
    assert update.kwargs["drives"] == drives(NOW.hour, "lazy")
    assert update.kwargs["drives"].nap == pytest.approx(drives(NOW.hour).nap * 2.5)
    # a live change reaches the next tick; no entry is the balanced cat
    store["default"] = Customization(personality="playful")
    unit = _unit(poll=_poll(snapshot=_snapshot()))
    assert _step(unit, events, store=store, time_of_day=True, tz=timezone.utc).kwargs["drives"] == drives(NOW.hour, "playful")
    unit = _unit(poll=_poll(snapshot=_snapshot()))
    assert _step(unit, events, time_of_day=True, tz=timezone.utc).kwargs["drives"] == drives(NOW.hour)


# ---- the cat's own bars (#135) ---------------------------------------------------

def _bars(unit, events, session, resets=RESETS, *, capped=False, view="limits", status="ok", **extra):
    """Step `unit` against a poll showing `session` percent, and return its bar_events."""
    limits = [LimitInfo(kind="session", percent=100.0, severity="exceeded", resets_at=resets,
                        is_active=True)] if capped else []
    snapshot = UsageSnapshot(session_pct=session, session_resets_at=resets, weekly_pct=20.0,
                             weekly_resets_at=WEEKLY_RESETS, limits=limits, fetched_at=NOW)
    unit["poller"] = _Worker(_poll(status=status, snapshot=snapshot))
    update = _step(unit, events, usage_state=_usage_state(view=view), **extra)
    return update.kwargs.get("bar_events"), unit["bars_seen"]


def test_the_first_fresh_poll_only_records(events):
    unit = _unit()
    assert _bars(unit, events, 40.0)[0] == (0, 0, 0)
    assert unit["bars_seen"]["pct"] == 40


def test_a_session_percent_going_up_is_a_move(events):
    unit = _unit()
    _bars(unit, events, 40.0)
    assert _bars(unit, events, 41.0)[0] == (1, 0, 0)
    # the same poll on the next tick, and a fall, are not
    assert _bars(unit, events, 41.0)[0] == (1, 0, 0)
    assert _bars(unit, events, 39.0)[0] == (1, 0, 0)
    assert _bars(unit, events, 42.0)[0] == (2, 0, 0)


def test_a_move_is_a_change_of_the_whole_percent_as_the_card_shows_it(events):
    unit = _unit()
    _bars(unit, events, 40.2)
    assert _bars(unit, events, 40.4)[0] == (0, 0, 0)           # both read 40
    assert _bars(unit, events, 40.6)[0] == (1, 0, 0)           # reads 41
    assert _bars(unit, events, 41.4)[0] == (1, 0, 0)           # still 41


def test_a_weekly_only_move_is_ignored(events):
    unit = _unit(poll=_poll(snapshot=_snapshot(session_pct=40.0, weekly_pct=20.0)))
    assert _step(unit, events).kwargs["bar_events"] == (0, 0, 0)
    unit["poller"] = _Worker(_poll(snapshot=_snapshot(session_pct=40.0, weekly_pct=30.0)))
    assert _step(unit, events).kwargs["bar_events"] == (0, 0, 0)


def test_a_stale_or_failed_poll_is_not_looked_at(events):
    unit = _unit()
    _bars(unit, events, 40.0)
    _bars(unit, events, 60.0, status="stale_token")
    assert unit["bars_seen"]["pct"] == 40
    assert _bars(unit, events, 40.0, status="api_error")[0] == (0, 0, 0)


def test_the_models_view_keeps_the_baseline_and_emits_nothing(events):
    unit = _unit(breakdown=_breakdown())
    _bars(unit, events, 40.0)
    update_args, seen = _bars(unit, events, 50.0, view="models")
    assert update_args is None and seen["pct"] == 50
    # back in the limits view that earlier tick-up does not fire
    assert _bars(unit, events, 50.0)[0] == (0, 0, 0)
    assert _bars(unit, events, 51.0)[0] == (1, 0, 0)


LATER = RESETS + timedelta(hours=5)


def test_a_rolled_over_window_with_a_drop_is_a_reset_not_a_move(events):
    unit = _unit()
    _bars(unit, events, 80.0)
    assert _bars(unit, events, 3.0, LATER)[0] == (0, 1, 0)
    assert _bars(unit, events, 3.0, LATER)[0] == (0, 1, 0)


def test_a_reset_needs_the_window_more_than_an_hour_on_and_a_drop(events):
    unit = _unit()
    _bars(unit, events, 80.0)
    assert _bars(unit, events, 3.0, RESETS + timedelta(hours=1))[0] == (0, 0, 0)   # exactly an hour
    unit = _unit()
    _bars(unit, events, 80.0)
    assert _bars(unit, events, 90.0, LATER)[0] == (1, 0, 0)                         # up, not a reset
    unit = _unit()
    _bars(unit, events, 80.0)
    assert _bars(unit, events, 3.0, RESETS)[0] == (0, 0, 0)                         # same window


def test_a_window_that_capped_does_not_pound_the_reset(events):
    unit = _unit()
    _bars(unit, events, 100.0, capped=True)
    assert _bars(unit, events, 2.0, LATER)[0] == (0, 0, 0)
    assert unit["bars_seen"]["capped"] is False
    # an uncapped window after it can reset again
    assert _bars(unit, events, 1.0, LATER + timedelta(hours=5))[0] == (0, 1, 0)


def test_a_reset_compares_with_the_last_resets_at_that_was_known(events):
    unit = _unit()
    _bars(unit, events, 80.0)
    _bars(unit, events, 80.0, None)
    assert unit["bars_seen"]["resets_at"] == RESETS
    assert _bars(unit, events, 3.0, LATER)[0] == (0, 1, 0)



def test_a_drop_seen_while_resets_at_was_missing_still_counts(events):
    # an idle window can poll back at 0% with no resets_at; the reset shows
    # once the next window has one
    unit = _unit()
    _bars(unit, events, 80.0)
    assert _bars(unit, events, 0.0, None)[0] == (0, 0, 0)
    assert _bars(unit, events, 1.0, LATER)[0] == (0, 1, 0)

# ---- the weekly window resets (#229) ---------------------------------------------

WEEK_LATER = WEEKLY_RESETS + timedelta(days=7)


def _weeks(unit, events, weekly, week_resets=WEEKLY_RESETS, *, session=40.0, resets=RESETS,
           capped_weekly=False, view="limits", status="ok"):
    """Step `unit` against a poll showing `weekly` percent, and return its bar_events."""
    limits = [LimitInfo(kind="weekly", percent=100.0, severity="exceeded", resets_at=week_resets,
                        is_active=True)] if capped_weekly else []
    snapshot = UsageSnapshot(session_pct=session, session_resets_at=resets, weekly_pct=weekly,
                             weekly_resets_at=week_resets, limits=limits, fetched_at=NOW)
    unit["poller"] = _Worker(_poll(status=status, snapshot=snapshot))
    return _step(unit, events, usage_state=_usage_state(view=view)).kwargs.get("bar_events")


def test_a_rolled_over_week_with_a_drop_counts_once(events):
    unit = _unit()
    assert _weeks(unit, events, 80.0) == (0, 0, 0)               # the first poll only records
    assert _weeks(unit, events, 2.0, WEEK_LATER) == (0, 0, 1)
    assert _weeks(unit, events, 2.0, WEEK_LATER) == (0, 0, 1)    # the same poll again
    assert _weeks(unit, events, 3.0, WEEK_LATER) == (0, 0, 1)


def test_a_week_needs_a_day_more_and_a_drop(events):
    unit = _unit()
    _weeks(unit, events, 80.0)
    assert _weeks(unit, events, 2.0, WEEKLY_RESETS + timedelta(days=1)) == (0, 0, 0)  # exactly a day
    unit = _unit()
    _weeks(unit, events, 80.0)
    assert _weeks(unit, events, 90.0, WEEK_LATER) == (0, 0, 0)                      # no drop
    unit = _unit()
    _weeks(unit, events, 80.0)
    assert _weeks(unit, events, 2.0, WEEKLY_RESETS) == (0, 0, 0)                    # same window


def test_a_week_that_capped_does_not_count(events):
    unit = _unit()
    _weeks(unit, events, 100.0, capped_weekly=True)
    assert _weeks(unit, events, 2.0, WEEK_LATER) == (0, 0, 0)
    assert unit["bars_seen"]["week_capped"] is False
    assert _weeks(unit, events, 1.0, WEEK_LATER + timedelta(days=7)) == (0, 0, 1)


def test_a_capped_session_does_not_hide_a_weekly_reset(events):
    unit = _unit()
    _bars(unit, events, 100.0, capped=True)
    assert _weeks(unit, events, 2.0, WEEK_LATER, session=100.0)[2] == 1


def test_a_stale_poll_or_the_models_view_does_not_fire_a_week(events):
    unit = _unit(breakdown=_breakdown())
    _weeks(unit, events, 80.0)
    _weeks(unit, events, 2.0, WEEK_LATER, status="stale_token")
    assert unit["bars_seen"]["week_resets_at"] == WEEKLY_RESETS
    assert _weeks(unit, events, 2.0, WEEK_LATER, view="models") is None
    assert _weeks(unit, events, 2.0, WEEK_LATER) == (0, 0, 0)    # the baseline moved with it


def test_a_week_and_a_session_resetting_together_count_only_the_week(events):
    unit = _unit()
    _weeks(unit, events, 80.0, session=80.0)
    assert _weeks(unit, events, 2.0, WEEK_LATER, session=3.0, resets=LATER) == (0, 0, 1)
    # the session baseline still moved, so its next window counts again on its own
    assert _weeks(unit, events, 2.0, WEEK_LATER, session=3.0, resets=LATER) == (0, 0, 1)


def test_a_session_reset_alone_still_counts_with_the_week_unchanged(events):
    unit = _unit()
    _weeks(unit, events, 20.0, session=80.0)
    assert _weeks(unit, events, 21.0, session=3.0, resets=LATER) == (0, 1, 0)


# ---- limits hover ------------------------------------------------------------

def _burning(*, start_pct=10.0, end_pct=40.0):
    tracker = BurnTracker()
    for offset, pct in ((0, start_pct), (600, end_pct)):
        tracker.add(UsageSnapshot(
            session_pct=pct, session_resets_at=RESETS, weekly_pct=20.0,
            weekly_resets_at=WEEKLY_RESETS, limits=[], fetched_at=NOW + timedelta(seconds=offset)))
    return tracker, NOW + timedelta(seconds=600)


def test_limits_hover_carries_the_raw_values_and_a_pace_per_bar():
    tracker, now = _burning()
    hover = _limits_hover_for(_snapshot(), tracker, {"dimmed": False, "stale_text": None}, now)
    assert hover.session.pace.status == PACE_CAPS
    assert hover.weekly.pace.status == PACE_WONT_CAP
    assert (hover.session.resets_at, hover.weekly.resets_at) == (RESETS, WEEKLY_RESETS)
    assert (hover.session.title, hover.weekly.title) == ("Session", "Week")
    assert not hover.session.capped and not hover.weekly.capped


@pytest.mark.parametrize("display", [{"dimmed": True}, {"stale_text": "as of 4:12 PM"}])
def test_limits_hover_drops_the_pace_for_dimmed_or_stale_numbers(display):
    tracker, now = _burning()
    hover = _limits_hover_for(_snapshot(), tracker, display, now)
    assert hover.session.pace == Pace(PACE_UNKNOWN) and hover.weekly.pace == Pace(PACE_UNKNOWN)


def test_limits_hover_marks_the_capped_bar():
    from tokitty.api import LimitInfo

    capped = LimitInfo(kind="session", percent=100.0, severity="exceeded", resets_at=RESETS, is_active=True)
    snapshot = UsageSnapshot(session_pct=100.0, session_resets_at=RESETS, weekly_pct=20.0,
                             weekly_resets_at=WEEKLY_RESETS, limits=[capped], fetched_at=NOW)
    hover = _limits_hover_for(snapshot, BurnTracker(), {}, NOW)
    assert hover.session.capped and not hover.weekly.capped


def test_limits_hover_credits_row_names_claude_spend_and_a_codex_balance():
    spend = replace(_snapshot(), credits_used=3.62, credits_limit=20.0)
    balance = replace(_snapshot(), credits_balance=12.4)
    assert _limits_hover_for(spend, BurnTracker(), {}, NOW).credits == "Extra usage: $3.62 / $20.00"
    assert _limits_hover_for(balance, BurnTracker(), {}, NOW).credits == "Credits: $12.40 left"
    assert _limits_hover_for(_snapshot(), BurnTracker(), {}, NOW).credits is None


def _failed(http_status=None, message=None):
    return PollResult(status="api_error", snapshot=None, message=message, fetched_at=NOW,
                      http_status=http_status)


def test_limits_hover_carries_the_failure_cause_and_the_last_good_time():
    last_good = _poll(snapshot=_snapshot(), fetched_at=NOW - timedelta(minutes=9))
    hover = _limits_hover_for(_snapshot(), BurnTracker(), {}, NOW, _failed(429), last_good)
    assert hover.problem == "rate limited (429)"
    assert hover.last_good_at == NOW - timedelta(minutes=9)


def test_limits_hover_has_no_failure_note_when_the_poll_worked():
    hover = _limits_hover_for(_snapshot(), BurnTracker(), {}, NOW, None, _poll(snapshot=_snapshot()))
    assert hover.problem is None and hover.last_good_at is None


def test_step_unit_api_error_puts_the_cause_in_the_hover_and_keeps_the_card_hint(events):
    last_good = _poll(snapshot=_snapshot(), fetched_at=NOW - timedelta(minutes=9))
    unit = _unit(poll=_failed(503, "HTTP 503 from usage endpoint"), last_good=last_good)
    kwargs = _step(unit, events, usage_state=_usage_state()).kwargs
    assert kwargs["hover"].problem == "server error (503)"
    assert kwargs["hover"].last_good_at == last_good.fetched_at


def test_step_unit_api_error_with_no_good_poll_still_explains_itself(events):
    unit = _unit(poll=_failed(None, "Network error reaching usage endpoint: timed out"))
    update = _step(unit, events, usage_state=_usage_state())
    assert update.kwargs["hint_text"] == "API hiccup, retrying"
    assert update.kwargs["hover"] == LimitsHover(session=None, weekly=None, problem="timed out")


def test_step_unit_a_working_poll_leaves_the_hover_without_a_failure_note(events):
    kwargs = _step(_unit(poll=_poll(snapshot=_snapshot())), events, usage_state=_usage_state()).kwargs
    assert kwargs["hover"].problem is None


def test_limits_hover_is_none_without_a_snapshot():
    assert _limits_hover_for(None, BurnTracker(), {}, NOW) is None
