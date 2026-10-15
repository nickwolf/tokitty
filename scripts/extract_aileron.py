"""Extract the Aileron Regular TTF that Pillow embeds in ImageFont.load_default.

Usage: python scripts/extract_aileron.py [OUTPUT.ttf]

Pillow 10.1+ stores the font as a base64 string inside the source of
PIL.ImageFont.load_default. This reads that string from the installed Pillow
and writes the decoded font. The output is committed as
tokitty/streamdock/fonts/Aileron-Regular.ttf, so Pillow is only needed to
re-run this script, never at run time.
"""
from __future__ import annotations

import base64
import inspect
import re
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tokitty" / "streamdock" / "fonts" / "Aileron-Regular.ttf"


def extract() -> bytes:
    from PIL import ImageFont

    source = inspect.getsource(ImageFont.load_default)
    match = re.search(r'b"""(.*?)"""', source, re.S)
    if not match:
        raise SystemExit("no embedded font found in PIL.ImageFont.load_default")
    return base64.b64decode(match.group(1))


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    data = extract()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"wrote {len(data)} bytes to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
