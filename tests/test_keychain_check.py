"""Pure-logic tests for freeze/keychain_check.py (#48, spec Q6).

The script is never imported as a package module (it stays stdlib-only and
lives under freeze/, same as freeze/gui_entry.py), so it's loaded by path
with importlib, the way tests/test_frozen.py loads freeze/gui_entry.py.

These tests never touch a real keychain or spawn `security`/`codesign`: they
exercise the functions that classify, parse, and reduce text/results the
same way the real script does, with sample text and result grids instead of
a macOS runner. The actual probe (spawning `security`, `codesign`, and two
built Tokitty.app binaries) can only run on the macos-latest runner.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "freeze" / "keychain_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("keychain_check_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kc = _load_module()


# ---------------------------------------------------------------------------
# classify_output
# ---------------------------------------------------------------------------


def test_classify_stale_token_with_keychain_source_is_no_prompt():
    stdout = "status: stale_token\ncredentials source: Keychain:Claude Code-credentials\n"
    verdict, detail = kc.classify_output(stdout, 0)
    assert verdict == "no_prompt"
    assert "pass-through read" in detail


def test_classify_stale_token_with_wrong_source_is_harness_error():
    # The scratch HOME/env isolation leaked a file-based source instead of
    # reading the synthetic Keychain item -- the result can't be trusted.
    stdout = "status: stale_token\ncredentials source: File:/Users/ci/.claude/.credentials.json\n"
    verdict, detail = kc.classify_output(stdout, 0)
    assert verdict == "harness_error"
    assert "isolation leak" in detail


def test_classify_stale_token_with_no_source_line_is_harness_error():
    stdout = "status: stale_token\n"
    verdict, _detail = kc.classify_output(stdout, 0)
    assert verdict == "harness_error"


def test_classify_keychain_denied_is_would_prompt():
    stdout = "status: keychain_denied\nmessage: User interaction is not allowed.\n"
    verdict, detail = kc.classify_output(stdout, 0)
    assert verdict == "would_prompt"
    assert "keychain_denied" in detail


def test_classify_credentials_unreachable_is_harness_error():
    # Spec Q6 step 5: anything other than stale_token/keychain_denied means
    # the harness is wrong, not that the prediction held.
    stdout = "status: credentials_unreachable\nmessage: no such file\n"
    verdict, _detail = kc.classify_output(stdout, 0)
    assert verdict == "harness_error"


def test_classify_nonzero_exit_is_harness_error_even_with_good_status_text():
    stdout = "status: stale_token\ncredentials source: Keychain:Claude Code-credentials\n"
    verdict, detail = kc.classify_output(stdout, 1)
    assert verdict == "harness_error"
    assert "exit 1" in detail


def test_classify_missing_status_line_is_harness_error():
    verdict, _detail = kc.classify_output("", 0)
    assert verdict == "harness_error"


# ---------------------------------------------------------------------------
# run_probe timeout handling (the harness-level "would prompt" path that
# never reaches classify_output at all: a timeout means a dialog would have
# appeared behind the headless runner, per spec Q6 step 4)
# ---------------------------------------------------------------------------


class _FakeTimeoutProc:
    """Mimics subprocess.Popen: first communicate() call times out, the
    reaping communicate() after the kill returns empty output."""

    def __init__(self):
        self.pid = 99999
        self._first_call = True

    def communicate(self, timeout=None):
        if self._first_call:
            self._first_call = False
            raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout)
        return b"", b""


def test_run_probe_timeout_is_would_prompt_and_kills_process_group(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(kc.subprocess, "Popen", lambda *a, **k: _FakeTimeoutProc())
    monkeypatch.setattr(kc.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    log = kc.Logger(tmp_path / "log.txt")
    verdict, detail = kc.run_probe(Path("/fake/binary"), tmp_path / "home", log)
    log.close()

    assert verdict == "would_prompt"
    assert "timed out" in detail
    assert killed == [(99999, kc.signal.SIGKILL)]


# ---------------------------------------------------------------------------
# parse_cdhash
# ---------------------------------------------------------------------------

CODESIGN_SAMPLE_A = """\
Executable=/work/dist-a/Tokitty.app/Contents/MacOS/Tokitty
Identifier=Tokitty
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=1234 flags=0x2(adhoc) hashes=20+7 location=embedded
Hash type=sha256 size=32
CDHash=aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00
Signature=adhoc
"""

CODESIGN_SAMPLE_B = """\
Executable=/work/dist-b/Tokitty.app/Contents/MacOS/Tokitty
Identifier=Tokitty
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=1234 flags=0x2(adhoc) hashes=20+7 location=embedded
Hash type=sha256 size=32
CDHash=0011223344556677889900112233445566778899
Signature=adhoc
"""


def test_parse_cdhash_extracts_hash():
    assert kc.parse_cdhash(CODESIGN_SAMPLE_A) == {"aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00"}


def test_parse_cdhash_empty_when_absent():
    assert kc.parse_cdhash("not a codesign output at all") == set()


def test_parse_cdhash_a_and_b_differ():
    assert kc.parse_cdhash(CODESIGN_SAMPLE_A) != kc.parse_cdhash(CODESIGN_SAMPLE_B)


# ---------------------------------------------------------------------------
# parse_keychain_list
# ---------------------------------------------------------------------------

LIST_KEYCHAINS_SAMPLE = '''    "/Users/ci/Library/Keychains/login.keychain-db"
    "/Library/Keychains/System.keychain"
'''


def test_parse_keychain_list_strips_quotes_and_whitespace():
    assert kc.parse_keychain_list(LIST_KEYCHAINS_SAMPLE) == [
        "/Users/ci/Library/Keychains/login.keychain-db",
        "/Library/Keychains/System.keychain",
    ]


def test_parse_keychain_list_empty_input():
    assert kc.parse_keychain_list("") == []


# ---------------------------------------------------------------------------
# compute_overall
# ---------------------------------------------------------------------------


def _grid(trusted_a="no_prompt", trusted_b="no_prompt", negative_a="would_prompt",
          negative_b="would_prompt", binary_a="would_prompt", binary_b="would_prompt"):
    return {
        "trusted": {"A": (trusted_a, "detail"), "B": (trusted_b, "detail")},
        "negative": {"A": (negative_a, "detail"), "B": (negative_b, "detail")},
        "binary-only": {"A": (binary_a, "detail"), "B": (binary_b, "detail")},
    }


def test_compute_overall_confirmed():
    overall, reason = kc.compute_overall(_grid())
    assert (overall, reason) == ("confirmed", None)


def test_compute_overall_any_cell_harness_error_wins():
    grid = _grid()
    grid["binary-only"]["B"] = ("harness_error", "boom")
    overall, reason = kc.compute_overall(grid)
    assert overall == "harness_error"
    assert "binary-only/B: boom" in reason


def test_compute_overall_broken_instrument_when_negative_passes():
    grid = _grid(negative_a="no_prompt")
    overall, reason = kc.compute_overall(grid)
    assert overall == "broken_instrument"
    assert "negative control" in reason


def test_compute_overall_trusted_a_denied_is_harness_error_not_disproved():
    # Round-1 fix: the positive control (trusted/A) failing means the
    # synthetic keychain/harness never showed a permitted read, so nothing
    # was established -- this must not be reported as a disproved prediction.
    grid = _grid(trusted_a="would_prompt")
    overall, reason = kc.compute_overall(grid)
    assert overall == "harness_error"
    assert "trusted/A was denied" in reason


def test_compute_overall_disproved_when_trusted_b_denied():
    grid = _grid(trusted_b="would_prompt")
    overall, reason = kc.compute_overall(grid)
    assert overall == "disproved"
    assert "trusted: B was denied where A passed" in reason


def test_compute_overall_disproved_when_binary_only_a_passes():
    grid = _grid(binary_a="no_prompt")
    overall, reason = kc.compute_overall(grid)
    assert overall == "disproved"
    assert "binary-only: A passed" in reason


def test_compute_overall_disproved_when_binary_only_b_passes():
    grid = _grid(binary_b="no_prompt")
    overall, reason = kc.compute_overall(grid)
    assert overall == "disproved"
    assert "binary-only: B passed" in reason


# ---------------------------------------------------------------------------
# exit_code_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overall,expected",
    [
        ("harness_error", 1),
        ("broken_instrument", 1),
        ("confirmed", 0),
        ("disproved", 0),
    ],
)
def test_exit_code_for(overall, expected):
    assert kc.exit_code_for(overall) == expected


# ---------------------------------------------------------------------------
# fold_cleanup_errors (round-1 fix: delete_keychain/restore_search_list
# failures must not be swallowed behind a clean-looking verdict)
# ---------------------------------------------------------------------------


def test_fold_cleanup_errors_noop_when_no_errors():
    assert kc.fold_cleanup_errors("confirmed", None, []) == ("confirmed", None)


def test_fold_cleanup_errors_overrides_a_clean_verdict():
    overall, reason = kc.fold_cleanup_errors("confirmed", None, ["could not delete keychain: boom"])
    assert overall == "harness_error"
    assert "cleanup failed after a confirmed result" in reason
    assert "could not delete keychain: boom" in reason


def test_fold_cleanup_errors_overrides_disproved_and_keeps_its_reason():
    overall, reason = kc.fold_cleanup_errors(
        "disproved", "binary-only: A passed", ["could not restore search list: boom"]
    )
    assert overall == "harness_error"
    assert "disproved (binary-only: A passed)" in reason


def test_fold_cleanup_errors_appends_to_an_existing_harness_error():
    overall, reason = kc.fold_cleanup_errors(
        "harness_error", "trusted/A was denied: ...", ["could not delete keychain: boom"]
    )
    assert overall == "harness_error"
    assert reason.startswith("trusted/A was denied")
    assert "cleanup also failed" in reason
    assert "could not delete keychain: boom" in reason
