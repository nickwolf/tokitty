#!/usr/bin/env python3
"""Stdlib-only CI probe for #48 Task 8 / spec Q6 ("macOS Keychain across
releases"). Tests the prediction that a new Tokitty build does not re-prompt
for Keychain access, because the process that actually asks the Security
framework is always `/usr/bin/security` -- `keychain.py` shells out to it and
never calls the framework in-process -- not Tokitty's own binary.

Never imports tokitty: every claim here comes from `security`/`codesign`
subprocess output and the probed binaries' own `--debug-print` stdout, the
same way a human running the spec's manual recipe would see it.

Usage:
  keychain_check.py --build-a <path to Tokitty.app> --build-b <path to Tokitty.app> --work <fresh dir>

Procedure (spec Q6 steps 1 to 6):
  1. Confirm build A and B really are different binaries (different CDHash),
     via `codesign -dv --verbose=4`. Equal CDHashes means the test would
     prove nothing -- a harness error, not a result.
  2. Create a throwaway keychain under --work, unlock it, prepend it to the
     user search list.
  3. Three items under service "Claude Code-credentials", one at a time:
     `trusted` (-T /usr/bin/security), `negative` (-T '', the control),
     `binary-only` (-T <build A's binary path>).
  4. For each item, run build A and build B with `--debug-print` under a
     scratch HOME with no accounts.json, no ~/.claude/.credentials.json, and
     every credentials-path override env var stripped. 30s external timeout;
     on timeout the whole process group is SIGKILLed and reaped so a
     `security -w` child (120s timeout, keychain.py:69) cannot outlive the
     case.
  5. Classify each run from its --debug-print output.
  6. Dump the keychain's ACL/partition list before every run, into the log.

Exit code: nonzero on any harness error, or if `negative` passes (the
instrument is broken). 0 for both CONFIRMED and DISPROVED outcomes -- the
verdict text is the evidence, not the job colour.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_FILENAME = "tk.keychain-db"
KEYCHAIN_PASSWORD = "ci"

PROBE_TIMEOUT = 30
PGREP_WAIT_TIMEOUT = 10
PGREP_POLL_INTERVAL = 0.5
SUBPROCESS_TIMEOUT = 30

ITEMS = ["trusted", "negative", "binary-only"]
BUILDS = ["A", "B"]

# Fake credentials, in the shape tokitty/credentials.py parses off a Keychain
# item: load_credentials() (credentials.py:163-188) does
# `json.loads(raw)` then `data.get("claudeAiOauth")`, and is_token_expired()
# (credentials.py:191-198) treats any `expiresAt` (epoch ms) at or before
# "now" as expired. providers/claude.py's build_fetch_fn checks
# is_token_expired() immediately after the credentials load and returns
# PollResult(status="stale_token", ...) there, before fetch_usage() (the
# network call) is ever reached.
FAKE_CREDENTIALS = json.dumps(
    {
        "claudeAiOauth": {
            "accessToken": "fake-access-token",
            "refreshToken": "fake-refresh-token",
            "expiresAt": 1,
            "scopes": ["user:inference"],
        }
    }
)

# Env vars the code reads via os.environ, found by grepping
# tokitty/credentials.py, tokitty/__main__.py and tokitty/paths.py (see the
# task report for the citations). Stripped from every probe's child env so
# nothing but the Keychain can decide the outcome:
#   TOKITTY_CREDENTIALS   credentials.ENV_OVERRIDE -- a direct file override
#                         that would short-circuit resolve_credentials_source
#                         before it ever reaches the Keychain branch.
#   TOKITTY_DEBUG_ACCOUNTS, TOKITTY_DEBUG_STATE -- __main__-read env vars
#                         that could alter the debug-print path.
#   LOCALAPPDATA, XDG_CONFIG_HOME -- paths.state_dir_path overrides. Inert on
#                         darwin (the darwin branch uses Path.home()
#                         unconditionally) but stripped anyway: HOME is the
#                         only lever that should matter here, and it is set
#                         explicitly per probe below.
CREDENTIALS_ENV_VARS = [
    "TOKITTY_CREDENTIALS",
    "TOKITTY_DEBUG_ACCOUNTS",
    "TOKITTY_DEBUG_STATE",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
]


class HarnessError(Exception):
    """A precondition failed badly enough that no further cell's result
    could be trusted: abort rather than keep probing."""


class Logger:
    """Writes every line to stdout and to a log file under --work, so the
    workflow can upload one artifact that has everything: the ACL/partition
    dumps, the codesign output, and each probe's raw stdout/stderr."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, text: str) -> None:
        if not text.endswith("\n"):
            text += "\n"
        sys.stdout.write(text)
        self._fh.write(text)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# ---------------------------------------------------------------------------
# pure helpers (unit-tested directly from tests/test_keychain_check.py)
# ---------------------------------------------------------------------------


def classify_output(stdout_text: str, returncode: int) -> Tuple[str, str]:
    """Classify a completed (non-timeout) `--debug-print` run.

    Returns (verdict, detail). verdict is one of:
      "no_prompt"     -- a pass-through Keychain read: a release would not
                         have shown a dialog here.
      "would_prompt"  -- status: keychain_denied. A dialog would have
                         appeared (and a headless runner can't click it).
      "harness_error" -- anything else: wrong exit, wrong status, or a
                         stale_token whose credentials source isn't the
                         Keychain (which would mean the scratch HOME/env
                         isolation leaked a file-based source instead).
    """
    status_match = re.search(r"^status: (\S+)$", stdout_text, re.MULTILINE)
    source_match = re.search(r"^credentials source: (.+)$", stdout_text, re.MULTILINE)
    status = status_match.group(1) if status_match else None
    source = source_match.group(1).strip() if source_match else None

    if returncode != 0:
        return "harness_error", f"exit {returncode}, status={status!r}, stdout={stdout_text!r}"

    if status == "keychain_denied":
        return "would_prompt", "status: keychain_denied"

    if status == "stale_token":
        expected_source = f"Keychain:{KEYCHAIN_SERVICE}"
        if source != expected_source:
            return (
                "harness_error",
                f"status: stale_token but credentials source is {source!r}, "
                f"expected {expected_source!r} (isolation leak?)",
            )
        return "no_prompt", "status: stale_token, credentials source: Keychain (pass-through read)"

    return "harness_error", f"unexpected status {status!r}; stdout={stdout_text!r}"


def parse_cdhash(codesign_text: str) -> set:
    """CDHash values found in `codesign -dv --verbose=4` output (one per
    hash algorithm an adhoc-signed Mach-O carries, e.g. sha1 and sha256)."""
    return set(re.findall(r"CDHash=([0-9a-fA-F]+)", codesign_text))


def parse_keychain_list(list_keychains_text: str) -> List[str]:
    """Parse `security list-keychains -d user` output into bare paths. Each
    line looks like `    "/Users/x/Library/Keychains/login.keychain-db"`."""
    paths = []
    for line in list_keychains_text.splitlines():
        line = line.strip()
        if not line:
            continue
        paths.append(line.strip('"'))
    return paths


def compute_overall(results: Dict[str, Dict[str, Tuple[str, str]]]) -> Tuple[str, Optional[str]]:
    """Reduce the 3x2 result grid to one overall verdict.

    Returns (verdict, reason) where verdict is one of "harness_error",
    "broken_instrument", "confirmed", "disproved". reason is a short
    explanation for the non-obvious ones (harness_error, disproved);
    None for confirmed and the exit-nonzero broken_instrument case (the
    table itself is the explanation there: both negative cells are visibly
    "no_prompt").
    """
    cell_errors = []
    for item in ITEMS:
        for build in BUILDS:
            verdict, detail = results[item][build]
            if verdict == "harness_error":
                cell_errors.append(f"{item}/{build}: {detail}")
    if cell_errors:
        return "harness_error", "; ".join(cell_errors)

    if results["negative"]["A"][0] == "no_prompt" or results["negative"]["B"][0] == "no_prompt":
        return (
            "broken_instrument",
            "the negative control (-T '') passed without a prompt; the instrument cannot see a real denial",
        )

    trusted_a_ok = results["trusted"]["A"][0] == "no_prompt"
    trusted_b_ok = results["trusted"]["B"][0] == "no_prompt"
    binary_only_a_denied = results["binary-only"]["A"][0] == "would_prompt"
    binary_only_b_denied = results["binary-only"]["B"][0] == "would_prompt"

    if trusted_a_ok and trusted_b_ok and binary_only_a_denied and binary_only_b_denied:
        return "confirmed", None

    reasons = []
    if trusted_a_ok and not trusted_b_ok:
        reasons.append("trusted: B was denied where A passed")
    elif trusted_b_ok and not trusted_a_ok:
        reasons.append("trusted: A was denied where B passed")
    elif not trusted_a_ok and not trusted_b_ok:
        reasons.append("trusted: both A and B were denied (no release would pass)")
    if not binary_only_a_denied:
        reasons.append("binary-only: A passed (ACL may key on Tokitty's own binary identity)")
    if not binary_only_b_denied:
        reasons.append("binary-only: B passed (ACL may key on Tokitty's own binary identity)")
    return "disproved", "; ".join(reasons)


def exit_code_for(overall: str) -> int:
    return 1 if overall in ("harness_error", "broken_instrument") else 0


def render_verdict_table(results: Dict[str, Dict[str, Tuple[str, str]]], overall: str, reason: Optional[str]) -> str:
    lines = ["item         A              B", "-" * 40]
    for item in ITEMS:
        a_verdict = results[item]["A"][0]
        b_verdict = results[item]["B"][0]
        lines.append(f"{item:<12} {a_verdict:<14} {b_verdict:<14}")
    lines.append("")
    label = {
        "confirmed": "CONFIRMED: no new prompt per release (Q6's prediction held)",
        "disproved": "DISPROVED: Q6's prediction did not hold",
        "broken_instrument": "BROKEN INSTRUMENT: the negative control passed",
        "harness_error": "HARNESS ERROR",
    }[overall]
    lines.append(f"VERDICT: {label}")
    if reason:
        lines.append(f"reason: {reason}")
    for item in ITEMS:
        for build in BUILDS:
            verdict, detail = results[item][build]
            lines.append(f"  {item}/{build}: {verdict} -- {detail}")
    return "\n".join(lines) + "\n"


def write_step_summary(table_text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## Keychain check (Task 8 / spec Q6)\n\n```\n")
        f.write(table_text)
        f.write("```\n")


# ---------------------------------------------------------------------------
# subprocess-backed helpers
# ---------------------------------------------------------------------------


def _run(argv: List[str], log: Logger, timeout: int = SUBPROCESS_TIMEOUT, check: bool = True):
    log.write(f"$ {' '.join(argv)}")
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"timed out after {timeout}s: {argv!r}") from exc
    except OSError as exc:
        raise HarnessError(f"failed to launch {argv!r}: {exc}") from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if stdout:
        log.write(stdout)
    if stderr:
        log.write(stderr)
    if check and proc.returncode != 0:
        raise HarnessError(f"{argv!r} exited {proc.returncode}: {stderr or stdout}")
    return proc, stdout, stderr


def _locate_binary(app_dir: Path) -> Path:
    binary = app_dir / "Contents" / "MacOS" / "Tokitty"
    if not binary.is_file():
        raise HarnessError(f"{binary} not found (expected {app_dir} to be a Tokitty.app bundle)")
    return binary


def check_cdhashes_differ(build_a_bin: Path, build_b_bin: Path, log: Logger) -> None:
    """Spec Q6 step 1 / acceptance criteria: builds A and B must really be
    different binaries, or the whole test proves nothing. Also done as a
    plain bash step in release.yml -- duplicated here so keychain_check.py
    stays self-verifying even run outside that workflow."""
    _proc_a, out_a, err_a = _run(["codesign", "-dv", "--verbose=4", str(build_a_bin)], log, check=False)
    _proc_b, out_b, err_b = _run(["codesign", "-dv", "--verbose=4", str(build_b_bin)], log, check=False)
    cdhash_a = parse_cdhash(out_a + err_a)
    cdhash_b = parse_cdhash(out_b + err_b)
    if not cdhash_a:
        raise HarnessError(f"codesign produced no CDHash for build A ({build_a_bin})")
    if not cdhash_b:
        raise HarnessError(f"codesign produced no CDHash for build B ({build_b_bin})")
    if cdhash_a == cdhash_b:
        raise HarnessError(f"build A and B have identical CDHash(es) {cdhash_a}; the test would prove nothing")
    log.write(f"CDHash A: {sorted(cdhash_a)}")
    log.write(f"CDHash B: {sorted(cdhash_b)}")


def capture_search_list(log: Logger) -> List[str]:
    _proc, stdout, _stderr = _run(["security", "list-keychains", "-d", "user"], log)
    return parse_keychain_list(stdout)


def create_keychain(keychain_path: Path, log: Logger) -> None:
    _run(["security", "create-keychain", "-p", KEYCHAIN_PASSWORD, str(keychain_path)], log)
    _run(["security", "set-keychain-settings", str(keychain_path)], log)
    _run(["security", "unlock-keychain", "-p", KEYCHAIN_PASSWORD, str(keychain_path)], log)


def prepend_to_search_list(keychain_path: Path, original_list: List[str], log: Logger) -> None:
    _run(["security", "list-keychains", "-d", "user", "-s", str(keychain_path)] + original_list, log)


def restore_search_list(original_list: List[str], log: Logger) -> None:
    _run(["security", "list-keychains", "-d", "user", "-s"] + original_list, log)


def delete_keychain(keychain_path: Path, log: Logger) -> None:
    _run(["security", "delete-keychain", str(keychain_path)], log, check=False)


def dump_keychain(keychain_path: Path, log: Logger) -> None:
    _run(["security", "dump-keychain", "-a", str(keychain_path)], log, check=False)


def add_item(keychain_path: Path, trusted_app: str, log: Logger) -> None:
    """Add the one `Claude Code-credentials` item for this case.

    `-a ""`: keychain.py's _base_command (keychain.py:40-44) only adds `-a`
    to the lookup when an account is given, and every call site in
    credentials.py passes account=None, so the item's account attribute
    cannot matter -- find-generic-password here matches on -s alone.
    """
    _run(
        [
            "security",
            "add-generic-password",
            "-a",
            "",
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            FAKE_CREDENTIALS,
            "-T",
            trusted_app,
            str(keychain_path),
        ],
        log,
    )


def remove_item(keychain_path: Path, log: Logger) -> None:
    _run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, str(keychain_path)], log, check=False)


def wait_for_no_security(log: Logger) -> None:
    deadline = time.monotonic() + PGREP_WAIT_TIMEOUT
    waited = False
    while time.monotonic() < deadline:
        proc = subprocess.run(["pgrep", "-x", "security"], capture_output=True)
        if proc.returncode != 0:
            if waited:
                log.write("pgrep -x security cleared")
            return
        waited = True
        time.sleep(PGREP_POLL_INTERVAL)
    proc = subprocess.run(["pgrep", "-x", "security"], capture_output=True)
    if proc.returncode == 0:
        pids = proc.stdout.decode("utf-8", errors="replace").strip()
        log.write(f"pgrep -x security still shows {pids!r} after {PGREP_WAIT_TIMEOUT}s")
        raise HarnessError(
            f"a `security` process was still running {PGREP_WAIT_TIMEOUT}s after the previous probe: {pids}"
        )


def _scratch_env(home: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    for key in CREDENTIALS_ENV_VARS:
        env.pop(key, None)
    return env


def run_probe(binary: Path, home: Path, log: Logger) -> Tuple[str, str]:
    """Run `<binary> --debug-print` under a scratch HOME, in its own process
    group, with a 30s external timeout. On timeout, SIGKILL the whole group
    and reap it: Tokitty's own `security -w` child has a 120s timeout
    (keychain.py:69) and must not outlive this case."""
    home.mkdir(parents=True, exist_ok=True)
    env = _scratch_env(home)
    argv = [str(binary), "--debug-print"]
    log.write(f"$ HOME={home} {' '.join(argv)}")
    try:
        proc = subprocess.Popen(
            argv,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return "harness_error", f"failed to launch {argv!r}: {exc}"

    try:
        stdout_b, stderr_b = proc.communicate(timeout=PROBE_TIMEOUT)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        stdout_b, stderr_b = proc.communicate()

    stdout_text = stdout_b.decode("utf-8", errors="replace")
    stderr_text = stderr_b.decode("utf-8", errors="replace")
    if stdout_text:
        log.write(stdout_text)
    if stderr_text:
        log.write(stderr_text)

    if timed_out:
        return "would_prompt", f"timed out after {PROBE_TIMEOUT}s (process group killed)"
    return classify_output(stdout_text, proc.returncode)


def trusted_app_for(item: str, build_a_bin: Path) -> str:
    if item == "trusted":
        return "/usr/bin/security"
    if item == "negative":
        return ""
    if item == "binary-only":
        return str(build_a_bin)
    raise ValueError(item)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-a", required=True, help="path to build A's Tokitty.app")
    parser.add_argument("--build-b", required=True, help="path to build B's Tokitty.app")
    parser.add_argument("--work", required=True, help="a fresh scratch directory")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    log = Logger(work / "keychain_check.log")

    results: Dict[str, Dict[str, Tuple[str, str]]] = {item: {} for item in ITEMS}
    keychain_path = work / KEYCHAIN_FILENAME
    original_search_list: Optional[List[str]] = None
    keychain_created = False
    harness_error: Optional[str] = None

    try:
        build_a_bin = _locate_binary(Path(args.build_a).resolve())
        build_b_bin = _locate_binary(Path(args.build_b).resolve())

        check_cdhashes_differ(build_a_bin, build_b_bin, log)

        original_search_list = capture_search_list(log)
        create_keychain(keychain_path, log)
        keychain_created = True
        prepend_to_search_list(keychain_path, original_search_list, log)

        for item in ITEMS:
            trusted_app = trusted_app_for(item, build_a_bin)
            add_item(keychain_path, trusted_app, log)
            try:
                for build, binary in (("A", build_a_bin), ("B", build_b_bin)):
                    wait_for_no_security(log)
                    dump_keychain(keychain_path, log)
                    home = work / "home" / f"{item}-{build}"
                    results[item][build] = run_probe(binary, home, log)
                    verdict, detail = results[item][build]
                    log.write(f"=> {item}/{build}: {verdict} -- {detail}")
            finally:
                remove_item(keychain_path, log)
    except HarnessError as exc:
        harness_error = str(exc)
        log.write(f"HARNESS ERROR: {harness_error}")
    finally:
        _cleanup(keychain_path, original_search_list, keychain_created, log)

    # Any cell a harness error cut short (including every cell, if the error
    # struck before the loop even started) is still owed a table entry.
    for item in ITEMS:
        for build in BUILDS:
            results[item].setdefault(build, ("harness_error", "not run (earlier harness error)"))

    if harness_error is not None:
        overall, reason = "harness_error", harness_error
    else:
        overall, reason = compute_overall(results)

    table_text = render_verdict_table(results, overall, reason)
    log.write(table_text)
    write_step_summary(table_text)
    log.close()
    return exit_code_for(overall)


def _cleanup(keychain_path: Path, original_search_list: Optional[List[str]], keychain_created: bool, log: Logger) -> None:
    """Best-effort: restore the search list, then delete the keychain.
    Logged, never raised -- a cleanup failure on an ephemeral CI runner must
    not mask the actual experiment's result."""
    if original_search_list is not None:
        try:
            restore_search_list(original_search_list, log)
        except HarnessError as exc:
            log.write(f"cleanup warning: could not restore search list: {exc}")
    if keychain_created:
        try:
            delete_keychain(keychain_path, log)
        except HarnessError as exc:
            log.write(f"cleanup warning: could not delete keychain: {exc}")


if __name__ == "__main__":
    sys.exit(main())
