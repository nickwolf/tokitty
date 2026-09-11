"""Windows-only fallback: locate Claude Code's credentials inside WSL.

Deliberately subprocess-only (no pathlib/UNC filesystem access) -- see
the module docstring in the design spec's Cross-platform architecture
section. All three functions accept an injectable `run` callable so
tests never invoke a real wsl.exe.
"""
from __future__ import annotations

import subprocess
from pathlib import PurePosixPath
from typing import Callable, List, Tuple

from tokitty.credentials import ENV_OVERRIDE, AmbiguousCredentialsError, CredentialsError

_CHECK_SCRIPT = 'for f in /home/*/.claude*/.credentials.json; do [ -f "$f" ] && echo "$f"; done'

# Same shape as _CHECK_SCRIPT, but keyed on a transcripts directory rather
# than on credentials. This is what makes the per-model view reachable for
# an API-key user: they have a full billing ledger on disk and no OAuth
# credentials file anywhere, so every credentials-keyed probe reports that
# they do not have Claude Code installed at all.
_PROJECTS_SCRIPT = (
    'for d in /home/*/.claude*/projects; do [ -d "$d" ] && echo "${d%/projects}"; done'
)

# wsl.exe is a console app; spawning it from a GUI process (pythonw.exe has
# no console of its own) without this flag flashes a visible terminal
# window on every poll. getattr(...) keeps this a no-op on non-Windows,
# where the constant doesn't exist but this module's tests still run.
_NO_CONSOLE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def list_wsl_distros(run: Callable = subprocess.run) -> List[str]:
    """Return the names of installed WSL distros, via `wsl.exe -l -q`."""
    try:
        result = run(
            ["wsl.exe", "-l", "-q"], capture_output=True, timeout=10, check=False, creationflags=_NO_CONSOLE_FLAGS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not list WSL distros: {exc}") from exc

    raw = result.stdout
    text = raw.decode("utf-16-le", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def list_running_distros(run: Callable = subprocess.run) -> List[str]:
    """Return the names of currently *running* WSL distros, via
    `wsl.exe --list --running --quiet`.

    Callers that would otherwise touch a \\\\wsl.localhost UNC path must
    check this first: accessing that path for a stopped distro silently
    boots it, and repeated access defeats WSL's idle auto-shutdown,
    leaving a multi-GB vmmem process running indefinitely. On any error
    this returns an empty list -- treat that as "nothing confirmed
    running", not as "nothing running": callers should back off rather
    than treat a probe failure as proof every distro is down.
    """
    try:
        result = run(
            ["wsl.exe", "--list", "--running", "--quiet"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=_NO_CONSOLE_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    raw = result.stdout
    text = raw.decode("utf-16-le", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def _credentials_paths_in_distro(distro: str, run: Callable = subprocess.run) -> List[str]:
    """Return WSL-side absolute paths to credentials files found under /home in a distro."""
    try:
        # --exec (not --) is required here: wsl.exe's default invocation
        # re-joins the argv after `--` before handing it to the distro's
        # shell, which mangles a multi-word `sh -c "<script>"` and leaves
        # variables like $u empty. --exec preserves the argv array as-is.
        result = run(
            ["wsl.exe", "-d", distro, "--exec", "sh", "-c", _CHECK_SCRIPT],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=_NO_CONSOLE_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    raw = result.stdout
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_wsl_credentials(run: Callable = subprocess.run) -> Tuple[str, str]:
    """Probe every installed WSL distro for a Claude Code credentials file.

    Returns (distro_name, wsl_side_path) for the single match. Raises
    AmbiguousCredentialsError if more than one match exists across all
    distros, or CredentialsError if none do.
    """
    distros = list_wsl_distros(run=run)
    matches: List[Tuple[str, str]] = []

    for distro in distros:
        for path in _credentials_paths_in_distro(distro, run=run):
            matches.append((distro, path))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        joined = ", ".join(f"{d}:{p}" for d, p in matches)
        raise AmbiguousCredentialsError(
            f"Multiple Claude Code installs found across WSL distros: {joined}. "
            f"Set {ENV_OVERRIDE} to the correct path."
        )

    raise CredentialsError(
        "No Claude Code credentials found in any WSL distro. "
        f"Set {ENV_OVERRIDE} to the correct path."
    )


def _claude_dirs_in_distro(distro: str, run: Callable = subprocess.run) -> List[str]:
    """WSL-side Claude config dirs in one distro, found by their projects/
    subdirectory. Mirrors _credentials_paths_in_distro, including its
    --exec argv handling."""
    try:
        result = run(
            ["wsl.exe", "-d", distro, "--exec", "sh", "-c", _PROJECTS_SCRIPT],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=_NO_CONSOLE_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    raw = result.stdout
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_all_wsl_claude_dirs(run: Callable = subprocess.run) -> List[Tuple[str, str]]:
    """Every (distro, wsl-side config dir) that has transcripts, whether or
    not it has OAuth credentials.

    find_all_wsl_credentials is deliberately left untouched: the limits
    view keeps its exact current behavior, and this is an additional,
    wider probe rather than a loosened one.
    """
    matches: List[Tuple[str, str]] = []
    for distro in list_wsl_distros(run=run):
        for config_dir in _claude_dirs_in_distro(distro, run=run):
            matches.append((distro, config_dir))
    return matches


def wsl_dir_exists(distro: str, posix_path: str, run: Callable = subprocess.run) -> bool:
    """Whether a directory exists inside a distro, without touching the
    UNC path (which would boot a stopped distro)."""
    try:
        result = run(
            ["wsl.exe", "-d", distro, "--exec", "sh", "-c", f'[ -d "{posix_path}" ]'],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=_NO_CONSOLE_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def find_all_wsl_credentials(run: Callable = subprocess.run) -> List[Tuple[str, str]]:
    """Return every (distro, wsl_side_path) credentials match across all
    installed WSL distros, without collapsing to one and without raising
    on zero or many matches. Used by the Accounts manager's discovery,
    which needs the full set; find_wsl_credentials keeps its
    single-match/raise contract for the existing single-account
    resolution callers."""
    distros = list_wsl_distros(run=run)
    matches: List[Tuple[str, str]] = []
    for distro in distros:
        for path in _credentials_paths_in_distro(distro, run=run):
            matches.append((distro, path))
    return matches


def _wsl_config_dir_windows_style(wsl_credentials_path: str) -> str:
    """Windows-style (backslash) relative path to the credentials file's
    parent directory, independent of that directory's basename. Fixes
    the old rsplit("/.claude/", 1) approach, which silently returned the
    whole input unsplit whenever the basename wasn't literally ".claude"
    (e.g. ".claude-work"), because str.rsplit returns the input
    unchanged when the separator isn't found."""
    config_posix = str(PurePosixPath(wsl_credentials_path).parent)
    return config_posix.lstrip("/").replace("/", "\\")


def wsl_config_dir_from_credentials(distro: str, wsl_credentials_path: str) -> str:
    """Derive the \\\\wsl.localhost UNC path to the WSL-side Claude Code
    config dir (the one containing settings.json) from a (distro,
    wsl-side credentials path) pair returned by find_wsl_credentials --
    never hardcode a username, always derive it from the actual
    credentials path found."""
    windows_style = _wsl_config_dir_windows_style(wsl_credentials_path)
    # Backslash built outside the f-string expression on purpose:
    # backslashes inside an f-string expression are a SyntaxError before
    # Python 3.12, and the CI matrix runs 3.10.
    unc_prefix = "\\\\wsl.localhost\\" + distro + "\\"
    return unc_prefix + windows_style


def wsl_sessions_dir_from_credentials(distro: str, wsl_credentials_path: str) -> str:
    """Derive the \\\\wsl.localhost UNC path to tokitty's sessions dir from
    a (distro, wsl-side credentials path) pair returned by
    find_wsl_credentials."""
    config_dir = wsl_config_dir_from_credentials(distro, wsl_credentials_path)
    return config_dir + "\\tokitty\\sessions"


def read_wsl_credentials(distro: str, wsl_path: str, run: Callable = subprocess.run) -> str:
    """Return the raw file contents of a credentials file inside WSL, via `wsl.exe cat`."""
    try:
        result = run(
            ["wsl.exe", "-d", distro, "--", "cat", wsl_path],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=_NO_CONSOLE_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not read credentials from WSL distro {distro}: {exc}") from exc

    if result.returncode != 0:
        raise CredentialsError(f"wsl.exe cat failed for {distro}:{wsl_path}")

    raw = result.stdout
    return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw


class WslCredentialsCache:
    """One credential sweep per process, shared by every caller that needs
    one (issue #52).

    Before this existed, a launch with no accounts.json ran the sweep three
    times over: once in resolve_activity_sessions, once in
    resolve_projects_dir, and once more on the Accounts discovery thread.
    The sweep is not cheap and it is not passive -- _credentials_paths_in_
    distro shells into each distro with `wsl.exe -d <name> --exec`, which
    starts a stopped distro. Running it three times per launch meant waking
    every installed distro three times, and once autostart (#20) shipped,
    on every login.

    The result is cached for the life of the process rather than on a TTL:
    every caller resolves once at startup, so a credentials file that
    appears mid-session was never picked up anyway. The lock is held across
    the sweep itself, which is what makes concurrent callers -- the Tk
    thread and the discovery thread race in practice -- share one sweep
    instead of each running their own.
    """

    def __init__(self, scan: Callable[[], List[Tuple[str, str]]] = None, enabled: bool = True):
        import threading

        self._scan = scan or find_all_wsl_credentials
        self._lock = threading.Lock()
        # enabled=False means there is nothing to sweep for: credentials
        # were already found natively, so a sweep would wake every distro to
        # answer a question that is already answered. run_discovery has
        # always put that guard on its own sweep. The resolvers went around
        # it and swept anyway, which is only visible now that both read the
        # same cache.
        self._done = not enabled
        self._matches: List[Tuple[str, str]] = []

    def all_matches(self) -> List[Tuple[str, str]]:
        """Every (distro, path) match, [] on any probe failure. Never raises:
        the discovery thread treats "no credentials anywhere" and "wsl.exe is
        missing from PATH" identically."""
        with self._lock:
            if not self._done:
                try:
                    self._matches = list(self._scan())
                except CredentialsError:
                    self._matches = []
                self._done = True
            return list(self._matches)

    def single(self) -> Tuple[str, str]:
        """find_wsl_credentials's contract, served from the cache: the one
        match, or AmbiguousCredentialsError / CredentialsError."""
        matches = self.all_matches()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            joined = ", ".join(f"{d}:{p}" for d, p in matches)
            raise AmbiguousCredentialsError(
                f"Multiple Claude Code installs found across WSL distros: {joined}. "
                f"Set {ENV_OVERRIDE} to the correct path."
            )
        raise CredentialsError(
            "No Claude Code credentials found in any WSL distro. "
            f"Set {ENV_OVERRIDE} to the correct path."
        )
