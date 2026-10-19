"""Copy diagnostics: the report must carry no names, paths or tokens, and the
stderr tee must keep a bounded tail without disturbing the real stream."""
import io
import sys
import threading

import pytest

from tokitty import diagnostics
from tokitty.diagnostics import StateFacts, StderrTee, build_report, redact

HOME = "/home/carol"
TOKEN = "sk-ant-oat01-" + "Ab3dEf5hIj7lMn9pQr1tUv3xYz5Bc7De9Fg1Hi3J"
HEX = "0123456789abcdef0123456789abcdef01234567"
BEARER = "Bearer abcDEF123456ghiJKL789012mnoPQR345"
WSL_DIR = r"\\wsl.localhost\Ubuntu\home\alice\.claude"
SECRET_LINE = (f"read {WSL_DIR}\\settings.json, opened {HOME}/.claude and C:\\Users\\Carol\\AppData for alice-work; "
               f"token {TOKEN} {BEARER} sha {HEX} label Nick's laptop")


def facts(**kw):
    base = dict(
        accounts_state="valid_non_empty", providers=["claude", "claude", "codex"],
        rows=[("claude", "installed"), ("claude", "not_installed"), ("codex", "unreachable")],
        names=["alice-work", "bob-personal"],
        config_dirs=[WSL_DIR, f"{HOME}/.claude-bob", r"C:\Users\Carol\.codex"],
        labels=["Nick's laptop"], crash_tail=[SECRET_LINE])
    base.update(kw)
    return StateFacts(**base)


def report(**kw):
    return build_report(
        facts=facts(**kw.pop("facts", {})), screens=[(1920, 1080, 1.25), (1280, 720, 1.0)],
        qt_version="6.12.0", pyside_version="6.12.0", version="v1.2.3",
        system=["os: Windows 11 (version 10.0.26100, no service pack)", "platform: win32",
                "python: 3.12.4", "frozen: no"],
        stderr_lines=[SECRET_LINE, "plain line"], home=HOME, **kw)


def test_report_leaks_nothing_and_keeps_the_facts():
    text = report()
    for secret in ("alice", "bob-personal", "Nick's laptop", "carol", "Carol", "Ubuntu",
                   TOKEN, "Ab3dEf5hIj7lMn9p", HEX, "abcDEF123456", HOME, "claude-bob"):
        assert secret not in text, secret
    assert "~" in text
    assert "<redacted>" in text and "<account>" in text and "<label>" in text
    assert "<dir>" in text
    for kept in ("tokitty: v1.2.3", "os: Windows 11 (version 10.0.26100, no service pack)",
                 "python: 3.12.4", "qt: 6.12.0, PySide6 6.12.0", "1. 1920x1080 at 1.25x",
                 "accounts: 3 (claude 2, codex 1)", "1. claude: installed",
                 "3. codex: unreachable", "crash.log", "plain line"):
        assert kept in text, kept


def test_report_has_one_row_per_account_and_no_extra_detail():
    text = report()
    assert text.count(": installed") == 1
    assert "Default" not in text


def test_absent_store_counts_the_default_account():
    text = report(facts={"accounts_state": "absent", "providers": ["claude"],
                         "rows": [("claude", "installed")], "crash_tail": []})
    assert "accounts: 1 (default, claude 1)" in text
    assert "crash.log" not in text


@pytest.mark.parametrize("raw", [
    r"C:\Users\Dana\x", "c:/users/dana/x", "/Users/dana/x", "/home/dana/x",
    r"\\wsl$\Debian\home\dana\x", "//wsl.localhost/Debian/home/dana/x",
    r"\\wsl.localhost\Debian\home\dana\x",
])
def test_generic_home_forms_become_a_tilde(raw):
    out = redact(f"at {raw} ok")
    assert "dana" not in out.lower() and "Debian" not in out
    assert "~" in out


def test_home_matches_both_separators_and_any_case():
    out = redact(r"c:\USERS\nick\a and C:/Users/Nick/b", home=r"C:\Users\Nick")
    assert "nick" not in out.lower()


def test_short_names_and_empty_values_are_skipped():
    out = redact("ab and the cat sat", names=["ab", ""], labels=["", "x"], config_dirs=[""])
    assert out == "ab and the cat sat"


def test_names_match_whole_occurrences_only():
    out = redact("work worked network", names=["work"])
    assert out == "<account> worked network"


@pytest.mark.parametrize("keep", [
    "Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39",
    "macOS-14.5-arm64-arm-64bit", "v0.2.2 2026-10-17T10:11:12", "10.0.26100",
    "~/AppData/Local/Programs/tokitty/_internal/PySide6/plugins",
    "the-quick-brown-fox-jumps-over-the-lazy-dog",
])
def test_versions_dates_and_paths_survive(keep):
    assert redact(f"x {keep} y") == f"x {keep} y"


def test_token_shapes_are_redacted():
    for secret in (TOKEN, "sk-proj-abcdefghijkl", BEARER, HEX,
                   "dGhpcyBpcyBhIHNlY3JldCB0b2tlbiB2YWx1ZQ=="):
        assert "<redacted>" in redact(f"v {secret} v")


def test_system_lines_are_plain_facts():
    lines = diagnostics.system_lines()
    assert [line.split(":")[0] for line in lines] == ["os", "platform", "python", "frozen"]


def test_read_state_gives_provider_and_state_only(tmp_path, monkeypatch):
    import json

    from tokitty import accounts_rows
    from tokitty.hooks_install import HookStatus

    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"name": "alice-work", "config_dir": str(tmp_path / "a")},
        {"name": "zed", "config_dir": str(tmp_path / "z"), "provider": "codex"}]}))
    (tmp_path / "customization.json").write_text(json.dumps(
        {"alice-work": {"label": "Nick's laptop"}}))
    (tmp_path / "crash.log").write_text("\n".join(f"l{i}" for i in range(80)) + "\n")
    monkeypatch.setattr(accounts_rows, "hook_status_for_dir",
                        lambda *a, **k: HookStatus("installed", "detail with alice-work"))
    got = diagnostics.read_state(tmp_path, None)
    assert got.rows == [("claude", "installed"), ("codex", "installed")]
    assert got.providers == ["claude", "codex"]
    assert "alice-work" in got.names and "Nick's laptop" in got.labels
    assert got.crash_tail[-1] == "l79" and len(got.crash_tail) == 50


# -- the tee ---------------------------------------------------------------------

def test_tee_keeps_only_the_last_fifty_lines():
    tee = StderrTee(io.StringIO())
    for i in range(80):
        tee.write(f"line {i}\n")
    tail = tee.tail()
    assert len(tail) == 50 and tail[0] == "line 30" and tail[-1] == "line 79"


def test_tee_joins_partial_lines_and_hides_the_unfinished_one():
    tee = StderrTee(None)
    tee.write("he")
    tee.write("llo\r\nwor")
    assert tee.tail() == ["hello"]
    tee.write("ld\n")
    assert tee.tail() == ["hello", "world"]


def test_tee_forwards_to_the_wrapped_stream():
    inner = io.StringIO()
    tee = StderrTee(inner)
    assert tee.write("a\nb") == 3
    tee.flush()
    assert inner.getvalue() == "a\nb"
    assert tee.isatty() is False

    class Bare:
        def write(self, data):
            return len(data)

    assert hasattr(tee, "getvalue") and not hasattr(StderrTee(Bare()), "fileno")
    assert hasattr(StderrTee(sys.__stderr__), "fileno") or sys.__stderr__ is None


def test_tee_over_none_only_buffers():
    tee = StderrTee(None)
    assert tee.write("x\n") == 2
    tee.flush()
    assert tee.isatty() is False and tee.encoding == "utf-8"
    assert not hasattr(tee, "fileno")
    assert tee.tail() == ["x"]


def test_install_is_idempotent_and_stderr_tail_reads_it(monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    first = diagnostics.install_stderr_tee()
    assert diagnostics.install_stderr_tee() is first and sys.stderr is first
    print("hello tail", file=sys.stderr)
    assert diagnostics.stderr_tail() == ["hello tail"]


def test_stderr_tail_is_empty_without_a_tee(monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert diagnostics.stderr_tail() == []


def test_tee_survives_several_writer_threads():
    tee = StderrTee(None)

    def spam(n):
        for i in range(200):
            tee.write(f"t{n} {i}\n")

    threads = [threading.Thread(target=spam, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tail = tee.tail()
    assert len(tail) == 50 and all(line.startswith("t") for line in tail)


def test_file_tail(tmp_path):
    path = tmp_path / "f.log"
    path.write_text("".join(f"{i}\n" for i in range(70)))
    assert diagnostics.file_tail(path)[0] == "20"
    assert diagnostics.file_tail(tmp_path / "missing") == []


def test_the_login_name_outside_a_path_is_scrubbed(monkeypatch):
    monkeypatch.setattr(diagnostics.getpass, "getuser", lambda: "dave")
    text = build_report(
        facts=facts(crash_tail=[]), screens=[], qt_version="6.12.0", pyside_version="6.12.0",
        version="v1.2.3", system=[], home=HOME,
        stderr_lines=["dave started tokitty", "carol's session ended", "davenport stays"])
    assert "dave started" not in text and "carol's" not in text
    assert "<user> started tokitty" in text and "<user>'s session ended" in text
    assert "davenport stays" in text
