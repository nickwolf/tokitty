"""Entry point of the bundled tokitty-hook runner.

Claude Code reads hook stdout and exit codes as control signals, and a
windowed PyInstaller build turns an escaped exception into a modal dialog,
so nothing may escape here, including a failed import.
"""
import sys

try:
    from tokitty import hook_writer

    hook_writer.main()
except BaseException:
    pass
sys.exit(0)
