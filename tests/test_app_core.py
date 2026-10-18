"""Direct tests for tokitty.app_core, the toolkit-free half of the app."""
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.accounts import Account
from tokitty.credentials import CredentialsError
from tokitty.activity import ActivityView
from tokitty.api import UsageSnapshot
from tokitty.behaviour import Drives, drives
from tokitty.app_core import PaneUpdate, _limits_hover_for, build_units, make_onboarder, step_unit
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
        "drives": Drives(),
    })
    assert [e[0] for e in events] == ["onboard", "note_write"]
    assert events[0][1:] == (poll, None)
    assert events[1][1:] == ("/home/x/.claude/tokitty", NOW, [])
    assert unit["last_good"] is poll
    assert [s.fetched_at for s in unit["burn"].samples] == [NOW]
    assert unit["deck_usage"] == (40.0, 20.0, False)


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
        "drives": Drives(),
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
        "drives": Drives(),
    })
    assert unit["last_good"] is None
    assert unit["burn"].samples == []
    assert unit["deck_usage"] == (0.0, 0.0, True)


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
    assert update.kwargs["drives"] == Drives()


@pytest.mark.parametrize("view", ["limits", "models"])
def test_step_unit_without_wander_never_plays(events, view):
    unit = _unit(poll=_poll(snapshot=_snapshot()), breakdown=_breakdown())
    update = _step(unit, events, usage_state=_usage_state(view=view), time_of_day=True,
                   tz=_MEXICO, wander=False)
    assert update.kwargs["drives"] == replace(drives(3), play=0.0)


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


def test_limits_hover_is_none_without_a_snapshot():
    assert _limits_hover_for(None, BurnTracker(), {}, NOW) is None
