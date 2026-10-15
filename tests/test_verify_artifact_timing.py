"""Tests for the interleaved hook timing steps of freeze/verify_artifact.py
(#158, #160): hook_timing, hook_timing_baseline and hook_timing_gate. The two
hooks are faked by patching run_child and time.perf_counter: each fake run
writes the session state file with the right seq and advances a fake clock by
a scripted duration, so nothing here launches a process."""
import importlib.util
import json
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO / "freeze" / "verify_artifact.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_artifact_timing_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


va = _load_module()


class FakeHooks:
    """Scripted frozen/baseline hooks. durations(side, round_no, n) gives the
    seconds the n-th warm call (1-based) of `side` takes in round `round_no`
    (the cold call is n == 0). fail(side, round_no, n) returns a failure kind
    ("exit", "seq") or None."""

    def __init__(self, durations, fail=None):
        self.durations = durations
        self.fail = fail or (lambda side, round_no, n: None)
        self.clock = 0.0
        self.calls = []  # (side, round_no, n)
        self.counts = {}

    def perf_counter(self):
        return self.clock

    def run_child(self, argv, env, input_bytes=None, timeout=None):
        side = argv[0]
        sessions = Path(argv[argv.index("--sessions-dir") + 1])
        session_id = json.loads(input_bytes)["session_id"]
        round_no = 2 if session_id.split("-")[1] == "r2" else 1
        key = (side, session_id)
        n = self.counts.get(key, 0)
        self.counts[key] = n + 1
        # cold runs use "<label>-cold", warm runs "<label>-warm"
        warm_n = n + 1 if session_id.endswith("-warm") else 0
        self.calls.append((side, round_no, warm_n))
        self.clock += self.durations(side, round_no, warm_n)
        failure = self.fail(side, round_no, warm_n)
        if failure == "exit":
            return types.SimpleNamespace(returncode=3, stdout=b""), None
        seq = n + 1 if failure != "seq" else 99
        (sessions / f"{session_id}.json").write_text(json.dumps({"seq": seq}), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=b""), None


@pytest.fixture
def make_ctx(tmp_path, monkeypatch):
    def _make(hooks, overhead_gate_ms=None, gate_ms=100):
        monkeypatch.setattr(va, "run_child", hooks.run_child)
        monkeypatch.setattr(va.time, "perf_counter", hooks.perf_counter)
        return {
            "env": {},
            "gate_ms": gate_ms,
            "overhead_gate_ms": overhead_gate_ms,
            "timing_setup": {
                "frozen_argv": ["frozen", "--sessions-dir", str(tmp_path / "f")],
                "frozen_sessions": tmp_path / "f",
                "baseline_argv": ["baseline", "--sessions-dir", str(tmp_path / "b")],
                "baseline_sessions": tmp_path / "b",
            },
        }

    return _make


def flat(frozen_s, baseline_s):
    return lambda side, round_no, n: frozen_s if side == "frozen" else baseline_s


def measured(ctx):
    ctx["timing_round"] = va._measure_round(ctx)
    return ctx


def test_interleaving_alternates_and_cold_runs_come_first(make_ctx):
    hooks = FakeHooks(flat(0.08, 0.05))
    rnd = va._measure_round(make_ctx(hooks))
    assert hooks.calls[:2] == [("frozen", 1, 0), ("baseline", 1, 0)]
    warm = hooks.calls[2:]
    assert len(warm) == 2 * va.PAIRED_WARM_RUNS
    for k in range(va.PAIRED_WARM_RUNS):
        first, second = warm[2 * k], warm[2 * k + 1]
        expected = ("frozen", "baseline") if k % 2 == 0 else ("baseline", "frozen")
        assert (first[0], second[0]) == expected
        assert first[2] == second[2] == k + 1
    assert rnd["order"][:4] == ["FB", "BF", "FB", "BF"]
    assert rnd["error"] is None


def test_paired_overheads_and_their_median(make_ctx):
    # Frozen runs get a slow-down on a few calls; the baseline gets a spike on
    # others. The median of differences is not the difference of medians.
    def durations(side, round_no, n):
        if side == "frozen":
            return 0.100 if n in (3, 4, 5) else 0.080
        return 0.060 if n in (10, 11) else 0.050

    rnd = va._measure_round(make_ctx(FakeHooks(durations)))
    overheads = rnd["paired_overhead_ms"]
    assert len(overheads) == va.PAIRED_WARM_RUNS
    assert overheads[0] == pytest.approx(30.0)
    assert overheads[2] == pytest.approx(50.0)  # n == 3
    assert overheads[9] == pytest.approx(20.0)  # n == 10
    assert rnd["overhead_median_ms"] == pytest.approx(30.0)
    assert rnd["frozen"]["median_ms"] == pytest.approx(80.0)
    assert rnd["baseline"]["median_ms"] == pytest.approx(50.0)
    assert len(rnd["frozen"]["timings_s"]) == va.PAIRED_WARM_RUNS


def test_overhead_gate_passes_on_paired_median(make_ctx):
    ctx = measured(make_ctx(FakeHooks(flat(0.080, 0.050)), overhead_gate_ms=40))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is True
    detail = result["detail"]
    assert detail["gate_kind"] == "overhead"
    assert detail["threshold_ms"] == 40
    assert detail["overhead_median_ms"] == pytest.approx(30.0)
    assert detail["frozen_median_ms"] == pytest.approx(80.0)
    assert detail["baseline_median_ms"] == pytest.approx(50.0)
    assert [r["round"] for r in detail["rounds"]] == [1]


def test_overhead_gate_fails_on_paired_median_even_if_absolute_would_pass(make_ctx):
    hooks = FakeHooks(flat(0.090, 0.050))  # frozen 90 < 100 absolute, overhead 40 > 30
    ctx = measured(make_ctx(hooks, overhead_gate_ms=30))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is False
    assert "overhead gate" in result["detail"]["error"]
    assert len(result["detail"]["rounds"]) == 2  # a miss is retried once


def test_absolute_gate_without_overhead_flag(make_ctx):
    ctx = measured(make_ctx(FakeHooks(flat(0.090, 0.050)), gate_ms=100))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is True
    assert result["detail"]["gate_kind"] == "absolute"
    assert result["detail"]["threshold_ms"] == 100

    ctx = measured(make_ctx(FakeHooks(flat(0.120, 0.050)), gate_ms=100))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is False
    assert "exceeds gate 100ms" in result["detail"]["error"]


def test_baseline_failure_fails_closed_under_overhead_gate_without_retry(make_ctx):
    hooks = FakeHooks(flat(0.080, 0.050), fail=lambda side, r, n: "exit" if (side, n) == ("baseline", 5) else None)
    ctx = measured(make_ctx(hooks, overhead_gate_ms=1000))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is False
    assert "measurement error" in result["detail"]["error"]
    assert "baseline warm run 5" in result["detail"]["error"]
    assert [r["round"] for r in result["detail"]["rounds"]] == [1]
    assert not any(c[1] == 2 for c in hooks.calls)
    base = va.step_hook_timing_baseline(ctx)
    assert base["ok"] is False
    assert "median_ms" not in base["detail"]
    assert len(base["detail"]["timings_s"]) == 5


def test_retry_first_round_fails_second_passes(make_ctx):
    # Round 1 has a slow frozen side, round 2 is clean.
    hooks = FakeHooks(lambda side, r, n: (0.120 if r == 1 else 0.070) if side == "frozen" else 0.050)
    ctx = measured(make_ctx(hooks, overhead_gate_ms=30))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is True
    detail = result["detail"]
    assert [(r["round"], r["ok"]) for r in detail["rounds"]] == [(1, False), (2, True)]
    assert detail["rounds"][0]["overhead_median_ms"] == pytest.approx(70.0)
    assert detail["overhead_median_ms"] == pytest.approx(20.0)  # deciding round
    assert "error" not in detail
    assert ctx["timing_round_2"]["error"] is None
    # Fresh session ids for the retry, so the seq checks start clean.
    assert (ctx["timing_setup"]["frozen_sessions"] / "verify-r2-warm.json").is_file()
    assert (ctx["timing_setup"]["baseline_sessions"] / "baseline-r2-warm.json").is_file()


def test_retry_both_rounds_fail(make_ctx):
    ctx = measured(make_ctx(FakeHooks(flat(0.120, 0.050)), overhead_gate_ms=30))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is False
    detail = result["detail"]
    assert [(r["round"], r["ok"]) for r in detail["rounds"]] == [(1, False), (2, False)]
    assert detail["threshold_ms"] == 30
    assert detail["error"]


def test_measurement_error_does_not_retry(make_ctx):
    hooks = FakeHooks(flat(0.080, 0.050), fail=lambda side, r, n: "seq" if (side, n) == ("frozen", 7) else None)
    ctx = measured(make_ctx(hooks, overhead_gate_ms=1000))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is False
    assert "seq 99 != 7" in result["detail"]["error"]
    assert [r["round"] for r in result["detail"]["rounds"]] == [1]
    assert "timing_round_2" not in ctx
    assert not any(c[1] == 2 for c in hooks.calls)


def test_retry_measurement_error_fails(make_ctx):
    hooks = FakeHooks(
        lambda side, r, n: 0.120 if side == "frozen" else 0.050,
        fail=lambda side, r, n: "exit" if (r, side, n) == (2, "baseline", 0) else None,
    )
    ctx = measured(make_ctx(hooks, overhead_gate_ms=30))
    result = va.step_hook_timing_gate(ctx)
    assert result["ok"] is False
    assert "baseline cold run" in result["detail"]["error"]
    assert len(result["detail"]["rounds"]) == 2


def test_cold_failure_reports_failing_side(make_ctx):
    hooks = FakeHooks(flat(0.080, 0.050), fail=lambda side, r, n: "exit" if (side, n) == ("frozen", 0) else None)
    rnd = va._measure_round(make_ctx(hooks))
    assert rnd["failed_side"] == "frozen"
    assert "frozen cold run" in rnd["error"]
    assert hooks.calls == [("frozen", 1, 0)]


def test_steps_report_both_sides_of_one_round(make_ctx, tmp_path, monkeypatch):
    hooks = FakeHooks(flat(0.080, 0.050))
    ctx = make_ctx(hooks, overhead_gate_ms=40)
    repo = tmp_path / "repo"
    (repo / "tokitty").mkdir(parents=True)
    (repo / "tokitty" / "hook_writer.py").write_text("# stub\n", encoding="utf-8")
    ctx.update(repo_root=repo, work=tmp_path / "work", settings_data={}, expected_command="x", sessions_dir_expected="y")
    ctx.pop("timing_setup")
    monkeypatch.setattr(va, "_release_b_realpath_check", lambda c: None)
    monkeypatch.setattr(va, "_hook_argv", lambda *a: ["frozen", "--sessions-dir", str(tmp_path / "f")])
    (tmp_path / "work").mkdir()

    timing = va.step_hook_timing(ctx)
    assert timing["ok"] is True
    d = timing["detail"]
    for key in ("cold_s", "timings_s", "min_s", "median_s", "max_s", "median_ms", "argv", "paired_overhead_ms", "overhead_median_ms", "order"):
        assert key in d
    assert d["median_ms"] == pytest.approx(80.0)

    base = va.step_hook_timing_baseline(ctx)
    assert base["ok"] is True
    b = base["detail"]
    assert b["median_ms"] == pytest.approx(50.0)
    assert b["frozen_median_ms"] == pytest.approx(80.0)
    assert b["diff_ms"] == pytest.approx(30.0)
    assert b["diff_kind"] == "median of paired differences"
    assert len(b["frozen_timings_s"]) == va.PAIRED_WARM_RUNS
    n_calls = len(hooks.calls)
    assert va.step_hook_timing_gate(ctx)["ok"] is True
    assert len(hooks.calls) == n_calls  # a passing first round measures nothing more
