"""The Copy diagnostics report, toolkit-free (Settings > General builds it).

Pure functions plus an in-memory stderr tee: tokitty keeps no log file, so the
tee holds the last lines the process printed, and nothing is written to disk.
The Qt layer gathers screens, clipboard and Qt versions and passes them in as
plain data. One redact() pass runs over the whole finished report, so a new
section cannot forget to scrub itself.
"""
from __future__ import annotations

import collections
import getpass
import platform
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, List, Optional, Sequence, Tuple

from tokitty.app_version import version_text

LOG_LINES = 50
MIN_SECRET_LEN = 3  # shorter names and labels would mangle ordinary words


class StderrTee:
    """A stderr stand-in that forwards to the real stream (None in a windowed
    frozen build, where it only buffers) and keeps the last complete lines."""

    def __init__(self, wrapped, keep: int = LOG_LINES) -> None:
        self._wrapped = wrapped
        self._lock = threading.Lock()
        self._lines: Deque[str] = collections.deque(maxlen=keep)
        self._partial = ""

    def write(self, data) -> int:
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        data = str(data)
        with self._lock:
            pieces = (self._partial + data).split("\n")
            self._partial = pieces.pop()
            for line in pieces:
                self._lines.append(line.rstrip("\r"))
        if self._wrapped is not None:
            return self._wrapped.write(data)
        return len(data)

    def flush(self) -> None:
        if self._wrapped is not None:
            self._wrapped.flush()

    def isatty(self) -> bool:
        return bool(self._wrapped is not None and self._wrapped.isatty())

    @property
    def encoding(self) -> str:
        return getattr(self._wrapped, "encoding", None) or "utf-8"

    def __getattr__(self, name):
        # fileno, errors, buffer...: only what the wrapped stream really has.
        wrapped = self.__dict__.get("_wrapped")
        if wrapped is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(wrapped, name)

    def tail(self, count: int = LOG_LINES) -> List[str]:
        with self._lock:
            return list(self._lines)[-count:]


def install_stderr_tee() -> StderrTee:
    """Put a tee on sys.stderr, once; a second call returns the first."""
    current = sys.stderr
    if isinstance(current, StderrTee):
        return current
    tee = StderrTee(current)
    sys.stderr = tee
    return tee


def stderr_tail(count: int = LOG_LINES) -> List[str]:
    current = sys.stderr
    return current.tail(count) if isinstance(current, StderrTee) else []


def file_tail(path, count: int = LOG_LINES) -> List[str]:
    """The last lines of a text file; [] when it is absent or unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\r\n") for line in collections.deque(f, maxlen=count)]
    except OSError:
        return []


# -- redaction -----------------------------------------------------------------

_SEP = r"[\\/]"
_HOME_PATTERNS = (
    # \\wsl$\Ubuntu\home\alice and \\wsl.localhost\Ubuntu\home\alice, either slash
    re.compile(rf"{_SEP}{{2}}wsl(?:\$|\.localhost){_SEP}+[^\\/\s\"']+{_SEP}+home{_SEP}+[^\\/\s\"']+",
               re.IGNORECASE),
    re.compile(rf"[A-Za-z]:{_SEP}+Users{_SEP}+[^\\/\s\"']+", re.IGNORECASE),
    re.compile(r"/Users/[^/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
)
_PREFIXED_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_LONG_RUN = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
_WORDISH = re.compile(r"[A-Za-z]{3,}")


def _loose_path(path: str) -> "re.Pattern[str]":
    """A pattern for `path` that takes either separator and any case."""
    parts = re.split(r"[\\/]+", path.strip())
    pattern = _SEP.join(re.escape(p) for p in parts)
    if parts[0] == "":
        pattern = _SEP + "?" + pattern  # a UNC path starts with two separators
    return re.compile(pattern, re.IGNORECASE)


def _whole(value: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", re.IGNORECASE)


def _secret_run(match: "re.Match[str]") -> str:
    run = match.group(0)
    # A version, a path or a kebab-case platform string: three or more real
    # words between separators. A random token almost never has that.
    pieces = re.split(r"[\-_/]", run)
    if sum(1 for p in pieces if _WORDISH.fullmatch(p)) >= 3:
        return run
    has_digit = any(c.isdigit() for c in run)
    mixed = any(c.islower() for c in run) and any(c.isupper() for c in run)
    return "<redacted>" if has_digit or mixed else run


def redact(text: str, *, home: Optional[str] = None, names: Sequence[str] = (),
           config_dirs: Sequence[str] = (), labels: Sequence[str] = (),
           users: Sequence[str] = ()) -> str:
    """Scrub a report: config dirs, the home in every form, account names, pane
    labels, the login name, then token-looking strings. Order matters: a config
    dir is usually under home, and an account name can sit inside a home path."""
    dirs = sorted({d for d in config_dirs if d and len(d.strip()) >= MIN_SECRET_LEN},
                  key=len, reverse=True)
    for d in dirs:
        text = _loose_path(d).sub("<dir>", text)
    if home and len(home.strip("\\/")) >= MIN_SECRET_LEN:
        text = _loose_path(home).sub("~", text)
    for pattern in _HOME_PATTERNS:
        text = pattern.sub("~", text)
    for values, token in ((names, "<account>"), (labels, "<label>"), (users, "<user>")):
        for value in sorted({v for v in values if v and len(v) >= MIN_SECRET_LEN},
                            key=len, reverse=True):
            text = _whole(value).sub(token, text)
    text = _PREFIXED_TOKEN.sub("<redacted>", text)
    text = _BEARER.sub("Bearer <redacted>", text)
    return _LONG_RUN.sub(_secret_run, text)


# -- facts ---------------------------------------------------------------------

def system_lines() -> List[str]:
    """OS, Python and build facts from the standard library."""
    if sys.platform == "win32":
        release, version, csd, _ptype = platform.win32_ver()
        os_text = f"Windows {release} (version {version}, {csd or 'no service pack'})"
    elif sys.platform == "darwin":
        os_text = f"macOS {platform.mac_ver()[0]} {platform.machine()}"
    else:
        os_text = platform.platform()
    return [
        f"os: {os_text}",
        f"platform: {sys.platform}",
        f"python: {platform.python_version()}",
        f"frozen: {'yes' if getattr(sys, 'frozen', False) else 'no'}",
    ]


@dataclass
class StateFacts:
    """What the worker reads off disk. rows are (provider, state) only; names,
    dirs and labels ride along solely so redact() knows what to remove."""

    accounts_state: str = "absent"
    providers: List[str] = field(default_factory=list)
    rows: List[Tuple[str, str]] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    config_dirs: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    crash_tail: List[str] = field(default_factory=list)


def _kind(provider: str) -> str:
    return provider if re.fullmatch(r"[a-z0-9_\-]{1,20}", provider or "") else "other"


def read_state(state_dir, distro_running: Optional[Callable[[str], bool]]) -> StateFacts:
    """Worker-thread: account hook states, names to scrub and the crash log
    tail. collect_rows may block on a WSL path, hence the worker."""
    from tokitty.accounts import load_accounts_result
    from tokitty.accounts_rows import collect_rows
    from tokitty.customize import load_customization
    from tokitty.frozen import CRASH_LOG_FILENAME

    facts = StateFacts()
    loaded = load_accounts_result(Path(state_dir))
    facts.accounts_state = loaded.state
    snapshot = collect_rows(state_dir, distro_running)
    for row in snapshot.rows:
        kind = _kind(row.provider)
        facts.providers.append(kind)
        facts.rows.append((kind, row.status.state if row.status is not None else "read-only"))
        if not row.is_default:  # "Default" is ours, not the user's
            facts.names.append(row.name)
        if row.config_dir:
            facts.config_dirs.append(row.config_dir)
    for account in loaded.accounts:
        facts.names.append(account.name)
        facts.config_dirs.append(account.config_dir)
    for custom in load_customization(Path(state_dir)).values():
        if custom.label:
            facts.labels.append(custom.label)
    facts.crash_tail = file_tail(Path(state_dir) / CRASH_LOG_FILENAME)
    return facts


def _accounts_line(facts: StateFacts) -> str:
    if facts.accounts_state == "malformed":
        return "accounts: unreadable"
    counts = collections.Counter(facts.providers)
    by_kind = ", ".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
    if facts.accounts_state == "absent":
        return f"accounts: 1 (default, {by_kind})" if by_kind else "accounts: 1 (default)"
    total = sum(counts.values())
    return f"accounts: {total} ({by_kind})" if total else "accounts: 0"


def build_report(*, facts: StateFacts, screens: Sequence[Tuple[int, int, float]],
                 qt_version: str, pyside_version: str, system: Optional[Sequence[str]] = None,
                 stderr_lines: Sequence[str] = (), version: Optional[str] = None,
                 home: Optional[str] = None, users: Optional[Sequence[str]] = None) -> str:
    """The finished, redacted report. screens are (width, height, ratio)."""
    lines = [f"tokitty: {version if version is not None else version_text()}"]
    lines += system if system is not None else system_lines()
    lines.append(f"qt: {qt_version}, PySide6 {pyside_version}")
    lines.append(f"screens: {len(screens)}")
    for i, (w, h, ratio) in enumerate(screens, start=1):
        lines.append(f"  {i}. {w}x{h} at {ratio:g}x")
    lines.append(_accounts_line(facts))
    for i, (kind, state) in enumerate(facts.rows, start=1):
        lines.append(f"  {i}. {kind}: {state}")
    lines.append("")
    lines.append(f"recent log (last {LOG_LINES} lines):")
    lines += [f"  {line}" for line in stderr_lines] or ["  (none)"]
    if facts.crash_tail:
        lines.append("")
        lines.append(f"crash.log (last {LOG_LINES} lines):")
        lines += [f"  {line}" for line in facts.crash_tail]
    if home is None:
        try:
            home = str(Path.home())
        except RuntimeError:
            home = None
    if users is None:
        users = _login_names(home)
    return redact("\n".join(lines) + "\n", home=home, names=facts.names,
                  config_dirs=facts.config_dirs, labels=facts.labels, users=users)


def _login_names(home: Optional[str]) -> List[str]:
    """The login name and the home folder's name, which can differ on Windows."""
    found = []
    try:
        found.append(getpass.getuser())
    except Exception:
        pass
    if home:
        found.append(Path(home).name)
    return [n for n in found if n]
