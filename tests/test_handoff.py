from datetime import datetime, timedelta, timezone

import pytest

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.app_core import PaneUpdate, apply_handoff, apply_hint, step_unit
from tokitty.handoff import Hint, Standing, handoff_hints
from tokitty.poller import PollResult
from tokitty.providers import get_provider
from tokitty.burn import BurnTracker

NOW = datetime(2030, 1, 2, 9, 0, tzinfo=timezone.utc)


def _s(key, session=10.0, weekly=10.0, capped=False, confirmed=True, label=None, kind="Claude"):
    return Standing(key, key if label is None else label, session, weekly, capped, confirmed, kind)



def _hints(standings, flags=None):
    return handoff_hints(standings, flags or {})


# ---- thresholds ------------------------------------------------------------

@pytest.mark.parametrize("session,weekly,is_near", [
    (84.9, 10, False), (85.0, 10, True), (10, 89.9, False), (10, 90.0, True),
])
def test_near_cap_enter_edges(session, weekly, is_near):
    _, flags = _hints([_s("a", session, weekly), _s("b")])
    assert flags["a"] is is_near


@pytest.mark.parametrize("session,weekly,roomy", [
    (49.9, 10, True), (50.0, 10, False), (10, 69.9, True), (10, 70.0, False),
])
def test_has_room_edges(session, weekly, roomy):
    hints, _ = _hints([_s("a", 90), _s("b", session, weekly)])
    assert ("b" in hints) is roomy


def test_hysteresis_enters_stays_and_leaves():
    flags = {}
    for standing, expect in [
        (_s("a", 85), True), (_s("a", 82), True), (_s("a", 10, 85), True),
        (_s("a", 79.9, 84), False), (_s("a", 82), False),
    ]:
        _, flags = _hints([standing, _s("b")], flags)
        assert flags["a"] is expect


def test_capped_counts_as_near_and_never_roomy():
    hints, flags = _hints([_s("a", 10, 10, capped=True), _s("b")])
    assert flags["a"] is True
    assert hints["a"] == Hint("near", "b has room")
    # A capped account never has room, even at low percentages.
    hints, _ = _hints([_s("a", 90), _s("b", 10, 10, capped=True)])
    assert hints == {}
    # Capped stays near through hysteresis even with low percentages.
    _, flags = _hints([_s("a", 10, 10, capped=True), _s("b")], {"a": True})
    assert flags["a"] is True


def test_no_hints_when_all_fine_all_near_or_single():
    assert _hints([_s("a", 60), _s("b", 60)])[0] == {}
    assert _hints([_s("a", 90), _s("b", 95)])[0] == {}
    assert _hints([_s("a", 90)])[0] == {}
    assert _hints([_s("a", 10)])[0] == {}
    assert _hints([])[0] == {}


def test_near_and_roomy_get_hints():
    hints, _ = _hints([_s("a", 90, label="Work"), _s("b", label="Home")])
    assert hints == {"a": Hint("near", "Home has room"), "b": Hint("room")}


def test_cross_provider_label():
    hints, _ = _hints([_s("claude", 95, label="Claude"), _s("codex", 5, label="Codex")])
    assert hints["claude"].text == "Codex has room"


def test_unlabelled_account_is_named_by_provider_only_when_it_differs():
    hints, _ = _hints([_s("a", 95, label=""), _s("b", 5, label="")])
    assert hints["a"].text == "Another account has room"
    hints, _ = _hints([_s("a", 95, label=""), _s("b", 5, label="", kind="Codex")])
    assert hints["a"].text == "Codex has room"


def test_several_roomy_picks_lowest_session_then_first():
    hints, _ = _hints([_s("a", 90), _s("b", 30), _s("c", 20), _s("d", 20)])
    assert hints["a"].text == "c has room"
    assert hints["b"].kind == hints["c"].kind == hints["d"].kind == "room"


def test_unconfirmed_is_ignored_and_keeps_its_flag():
    # A dimmed roomy account is not a destination.
    hints, flags = _hints([_s("a", 90), _s("b", 5, confirmed=False)], {"b": True})
    assert hints == {}
    assert flags == {"a": True, "b": True}
    # A dimmed near account does not hint and does not make others roomy-for-it.
    hints, flags = _hints([_s("a", 10, confirmed=False), _s("b", 10)], {"a": True})
    assert hints == {}
    assert flags["a"] is True


def test_flags_are_not_mutated():
    flags = {"a": False}
    _hints([_s("a", 90), _s("b")], flags)
    assert flags == {"a": False}


# ---- apply_hint ------------------------------------------------------------

def _update(state="content", dimmed=False):
    return PaneUpdate("limits", {"state": state, "dimmed": dimmed, "hint_text": None})


@pytest.mark.parametrize("state", ["sleeping", "content"])
def test_perk_up_lifts_a_resting_cat(state):
    out = apply_hint(_update(state), Hint("room"), state)
    assert out.kwargs["state"] == "interested"


@pytest.mark.parametrize("sprite,ladder", [
    ("working", "content"), ("thinking", "sleeping"), ("permission", "content"),
    ("interested", "interested"), ("alert", "alert"), ("panicked", "panicked"),
    ("flopped", "flopped"),
])
def test_perk_up_leaves_everything_else(sprite, ladder):
    update = _update(sprite)
    assert apply_hint(update, Hint("room"), ladder) is update


def test_perk_up_not_when_dimmed():
    update = _update("sleeping", dimmed=True)
    assert apply_hint(update, Hint("room"), "sleeping") is update


def test_near_adds_text_and_only_an_alert_cat_turns_tired():
    hint = Hint("near", "Codex has room")
    out = apply_hint(_update("alert"), hint, "alert")
    assert out.kwargs["handoff_text"] == "Codex has room"
    assert out.kwargs["state"] == "tired"
    for state in ("panicked", "flopped", "stirring", "waking", "activate", "working", "permission", "content"):
        assert apply_hint(_update(state), hint, state).kwargs["state"] == state
    assert apply_hint(_update("panicked"), hint, "panicked").kwargs["handoff_text"] == "Codex has room"


def test_apply_hint_passes_models_and_none_through():
    models = PaneUpdate("models", {"state": "content"})
    assert apply_hint(models, Hint("room"), "content") is models
    update = _update()
    assert apply_hint(update, None, "content") is update


# ---- step_unit standing ----------------------------------------------------

class _Worker:
    def __init__(self, latest=None):
        self.latest = latest

    def get_latest(self):
        return self.latest


class _Notifier:
    def check(self, *a):
        pass


def _unit(key, poll, last_good=None, kind=None):
    return {
        "poller": _Worker(poll), "usage": _Worker(), "watcher": _Worker(),
        "provider": get_provider(kind), "key": key, "last_good": last_good,
        "burn": BurnTracker(), "sessions_dir": "/home/x/.claude/tokitty/sessions",
    }


def _snap(session=40.0, weekly=20.0, limits=()):
    later = NOW + timedelta(hours=5)
    return UsageSnapshot(session_pct=session, session_resets_at=later, weekly_pct=weekly,
                         weekly_resets_at=NOW + timedelta(days=4), limits=list(limits), fetched_at=NOW)


def _poll(status="ok", snapshot=None):
    return PollResult(status=status, snapshot=snapshot, message=None, fetched_at=NOW)


def _step(unit, view="limits", store=None):
    return step_unit(
        unit,
        usage_state={"view": view, "window": "7d", "readout": "cost", "budgets": {}, "onboarding": 99,
                     "notes": (False, 90, 95), "ntfy": (False, "", "", "")},
        notifier=_Notifier(), customization_store=store or {}, notes_removed=set(),
        onboard=lambda *a: None, now=NOW,
    )


def test_standing_for_a_healthy_unit():
    unit = _unit("a", _poll(snapshot=_snap(40, 20)))
    _step(unit)
    assert unit["handoff_standing"] == Standing("a", "", 40.0, 20.0, False, True, kind="Claude")
    assert unit["handoff_ladder"] == "content"


def test_standing_uses_the_custom_label():
    from tokitty.customize import Customization

    unit = _unit("a", _poll(snapshot=_snap()))
    _step(unit, store={"a": Customization(label=" Work ")})
    assert unit["handoff_standing"].label == "Work"


def test_standing_capped_from_a_binding_limit():
    capped = LimitInfo("session", 100.0, "exceeded", NOW + timedelta(hours=2), True)
    unit = _unit("a", _poll(snapshot=_snap(100, 30, [capped])))
    _step(unit)
    assert unit["handoff_standing"].capped is True
    assert unit["handoff_standing"].confirmed is True


def test_standing_unconfirmed_when_dimmed():
    good = _poll(snapshot=_snap(40, 20))
    unit = _unit("a", _poll(status="keychain_denied"), last_good=good)
    _step(unit)
    assert unit["handoff_standing"].confirmed is False
    unit = _unit("a", _poll(status="api_error"))
    _step(unit)
    assert unit["handoff_standing"].confirmed is False


def test_standing_none_for_models_view_and_no_result():
    unit = _unit("a", _poll(snapshot=_snap()))
    _step(unit, view="models")
    assert unit["handoff_standing"] is None
    unit = _unit("a", None)
    assert _step(unit) is None
    assert unit["handoff_standing"] is None


# ---- apply_handoff (the tick's second pass) --------------------------------

def test_apply_handoff_two_units_carries_flags():
    near = _unit("a", _poll(snapshot=_snap(90, 10)))
    roomy = _unit("b", _poll(snapshot=_snap(5, 5)), kind="codex")
    stepped = [(near, _step(near)), (roomy, _step(roomy))]
    out = apply_handoff(stepped)
    assert out[0].kwargs["handoff_text"] == "Codex has room"
    assert "handoff_text" not in out[1].kwargs
    assert out[1].kwargs["state"] == "interested"
    assert near["handoff_near"] is True and roomy["handoff_near"] is False


def test_apply_handoff_single_unit_is_unchanged():
    only = _unit("a", _poll(snapshot=_snap(90, 10)))
    update = _step(only)
    assert apply_handoff([(only, update)]) == [update]


def test_apply_handoff_ignores_models_view_and_missing_updates():
    near = _unit("a", _poll(snapshot=_snap(90, 10)))
    models = _unit("b", _poll(snapshot=_snap(5, 5)))
    idle = _unit("c", None)
    stepped = [(near, _step(near)), (models, _step(models, view="models")), (idle, _step(idle))]
    out = apply_handoff(stepped)
    assert "handoff_text" not in out[0].kwargs
    assert out[1].kind == "models" and out[2] is None
