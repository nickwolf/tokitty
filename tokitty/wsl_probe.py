"""Windows-only fallback: locate Claude Code's credentials inside WSL.

Deliberately subprocess-only (no pathlib/UNC filesystem access) -- see
the module docstring in the design spec's Cross-platform architecture
section. All three functions accept an injectable `run` callable so
tests never invoke a real wsl.exe.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Tuple

from tokitty.credentials import ENV_OVERRIDE, AmbiguousCredentialsError, CredentialsError

_CHECK_SCRIPT = 'for u in /home/*; do f="$u/.claude/.credentials.json"; [ -f "$f" ] && echo "$f"; done'

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


def wsl_sessions_dir_from_credentials(distro: str, wsl_credentials_path: str) -> str:
    """Derive the \\\\wsl.localhost UNC path to tokitty's sessions dir from
    a (distro, wsl-side credentials path) pair returned by
    find_wsl_credentials -- never hardcode a username, always derive it
    from the actual credentials path found."""
    home = wsl_credentials_path.rsplit("/.claude/", 1)[0]
    windows_style_home = home.lstrip("/").replace("/", "\\")
    return f"\\\\wsl.localhost\\{distro}\\{windows_style_home}\\.claude\\tokitty\\sessions"


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
