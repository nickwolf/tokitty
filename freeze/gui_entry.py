import os
import sys
import traceback


def _fallback_state_dir():
    """Computed inline, with the same rules as tokitty.paths.state_dir_path,
    for the case where even tokitty.frozen fails to import: this entry must
    not depend on any part of the broken bundle to log its own crash."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "Tokitty")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Tokitty")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tokitty")


def _run():
    try:
        from tokitty.frozen import run_gui_entry
    except Exception:
        try:
            state_dir = _fallback_state_dir()
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "crash.log"), "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        return 1

    def main():
        from tokitty.__main__ import main as real_main

        return real_main()

    return run_gui_entry(main)


sys.exit(_run())
