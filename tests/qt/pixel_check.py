"""Pixel reference images for the Qt widget (#128): build them, compare them,
and regenerate them.

Importable by test_pixel_refs.py and runnable as a script:

    python tests/qt/pixel_check.py update [name ...]   # show the diff, then write references
    python tests/qt/pixel_check.py dump DIR --dpr N    # internal: render at one device pixel ratio

A process has one QApplication and QT_SCALE_FACTOR must be set before it
exists, so every dpr other than the test process's own is rendered in a child
process through `dump`. References live in tests/qt/pixel_refs/: one indexed
PNG sheet per sprite state (cat/<state>@<dpr>x.png) and one RGBA PNG per
layout (card/<layout>@<dpr>x.png).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
REFS = ROOT / "tests" / "qt" / "pixel_refs"
DPRS = (1, 2)

if __name__ == "__main__":
    # Run as a script, tokitty must come from this checkout, not an installed one.
    sys.path.insert(0, str(ROOT))
    sys.meta_path[:] = [f for f in sys.meta_path if "Editable" not in repr(f)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QFont, QImage, QPainter  # noqa: E402

from tokitty import sprites  # noqa: E402
from tokitty import theme as T  # noqa: E402
from tokitty.customize import Customization  # noqa: E402
from tokitty.qt.widget import TokittyQtWidget  # noqa: E402
from tokitty.streamdock import render as key_render  # noqa: E402
from tokitty.usage_display import UsageRow, UsageView  # noqa: E402

Rect = Tuple[int, int, int, int]  # x0, y0, x1, y1 in device pixels, x1/y1 exclusive
MASK_PAD = 2        # logical px added around each text rect; the halo reaches 1
MASK_COLOR = b"\xff\x00\xff\xff"

LAYOUTS = ("default", "halo", "low_opacity", "cat_right", "cat_top", "models")
# Layouts whose text sits on a translucent card: Intel and AMD round it differently.
TRANSLUCENT_LAYOUTS = frozenset({"low_opacity"})
COATS = [(c, p) for c in sprites.COLORWAYS for p in sprites.PATTERNS]


# ---- images -----------------------------------------------------------------

@dataclass(frozen=True)
class Img:
    w: int
    h: int
    data: bytes  # RGBA8888, no row padding


@dataclass
class Rendered:
    img: Img
    rects: List[Rect] = field(default_factory=list)
    always_mask: bool = False  # mask the text on every OS, not just macOS
    dpr: float = 1.0


@dataclass
class Diff:
    count: int
    bbox: Optional[Rect]  # inclusive x0, y0, x1, y1; None when only the size differs
    first: List[Tuple[int, int, bytes, bytes]]
    points: List[Tuple[int, int]]
    note: str = ""

    def summary(self) -> str:
        if self.bbox is None:
            return self.note
        x0, y0, x1, y1 = self.bbox
        return f"{self.count} pixels differ, bbox ({x0},{y0})-({x1},{y1})"


def qimage_to_img(image: QImage) -> Img:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = image.width(), image.height(), image.bytesPerLine()
    raw = bytes(image.constBits())
    if bpl == w * 4:
        return Img(w, h, raw[: w * h * 4])
    return Img(w, h, b"".join(raw[r * bpl: r * bpl + w * 4] for r in range(h)))


def read_png(path: Path) -> Img:
    with Image.open(path) as im:
        im = im.convert("RGBA")
        return Img(im.width, im.height, im.tobytes())


def write_png(path: Path, img: Img, indexed: bool = False) -> None:
    """Write RGBA, or an exact-palette indexed PNG when it fits in 256 colours.
    The indexed file is read back and must give the same bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.frombytes("RGBA", (img.w, img.h), img.data)
    colors = im.getcolors(maxcolors=256) if indexed else None
    if colors is None:
        im.save(path, "PNG", optimize=True)
        return
    palette = sorted(c for _n, c in colors)
    lookup = {int.from_bytes(bytes(c), sys.byteorder): i for i, c in enumerate(palette)}
    pixels = memoryview(img.data).cast("I")
    out = Image.frombytes("P", (img.w, img.h), bytes(map(lookup.__getitem__, pixels)))
    out.putpalette([v for c in palette for v in c[:3]])
    out.save(path, "PNG", optimize=True, transparency=bytes(c[3] for c in palette))
    if read_png(path).data != img.data:
        raise RuntimeError(f"indexed PNG round trip changed pixels: {path}")


# ---- comparing --------------------------------------------------------------

def apply_mask(img: Img, rects: Sequence[Rect]) -> bytes:
    if not rects:
        return img.data
    data = bytearray(img.data)
    for x0, y0, x1, y1 in rects:
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, img.w), min(y1, img.h)
        if x1 <= x0:
            continue
        span = MASK_COLOR * (x1 - x0)
        for y in range(y0, y1):
            data[(y * img.w + x0) * 4:(y * img.w + x1) * 4] = span
    return bytes(data)


def compare(actual: Img, ref: Img, rects: Sequence[Rect] = ()) -> Optional[Diff]:
    """None when the two match outside `rects`, else where they differ."""
    if (actual.w, actual.h) != (ref.w, ref.h):
        return Diff(0, None, [], [], f"size {actual.w}x{actual.h} against reference {ref.w}x{ref.h}")
    a, b = apply_mask(actual, rects), apply_mask(ref, rects)
    if a == b:
        return None
    stride = actual.w * 4
    points: List[Tuple[int, int]] = []
    first: List[Tuple[int, int, bytes, bytes]] = []
    for y in range(actual.h):
        ra, rb = a[y * stride:(y + 1) * stride], b[y * stride:(y + 1) * stride]
        if ra == rb:
            continue
        for x in range(actual.w):
            pa, pb = ra[x * 4:x * 4 + 4], rb[x * 4:x * 4 + 4]
            if pa != pb:
                points.append((x, y))
                if len(first) < 5:
                    first.append((x, y, pa, pb))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return Diff(len(points), (min(xs), min(ys), max(xs), max(ys)), first, points)


def write_compare(path: Path, old: Img, new: Img, diff: Diff) -> None:
    """Old | new | difference side by side; differing pixels are red."""
    w, h = max(old.w, new.w), max(old.h, new.h)
    gap = 4
    sheet = Image.new("RGBA", (w * 3 + gap * 2, h), (40, 40, 40, 255))
    sheet.paste(Image.frombytes("RGBA", (old.w, old.h), old.data), (0, 0))
    sheet.paste(Image.frombytes("RGBA", (new.w, new.h), new.data), (w + gap, 0))
    if diff.bbox is not None:
        panel = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        for x, y in diff.points:
            panel.putpixel((x, y), (255, 0, 0, 255))
        sheet.paste(panel, (w * 2 + gap * 2, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "PNG")


def diff_dir() -> Path:
    return Path(os.environ.get("TOKITTY_PIXEL_DIFF_DIR") or ROOT / "build" / "pixel-diff")


def flat(name: str) -> str:
    return name.replace("/", "__")


# ---- the widget under test --------------------------------------------------

class PixelWidget(TokittyQtWidget):
    """The real widget with the bundled Aileron instead of the platform font
    (so Linux and Windows rasterize alike), and a record of where its text
    landed (so the mask can cover exactly the glyphs)."""

    def __init__(self, *args, **kwargs):
        self.text_rects: List[QRectF] = []
        super().__init__(*args, **kwargs)

    def _make_font(self, points: int, bold: bool) -> QFont:
        font = QFont(key_render.register_font())
        font.setPixelSize(round(points * 96 / 72))
        font.setBold(bold)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
        return font

    def _text(self, p: QPainter, color, rect: QRectF, flags, text: str, halo: bool = False) -> None:
        super()._text(p, color, rect, flags, text, halo)
        # After drawing: boundingRect is empty while the pen is NoPen, and the
        # card paint leaves it that way until a text sets its colour.
        if text:
            self.text_rects.append(p.transform().mapRect(p.boundingRect(rect, int(flags), text)))

    def grab_img(self) -> Img:
        self.text_rects.clear()
        return qimage_to_img(self.grab().toImage())

    def mask_rects(self) -> List[Rect]:
        dpr = self.devicePixelRatioF()
        out: List[Rect] = []
        for r in self.text_rects:
            out.append((math.floor((r.left() - MASK_PAD) * dpr), math.floor((r.top() - MASK_PAD) * dpr),
                        math.ceil((r.right() + MASK_PAD) * dpr), math.ceil((r.bottom() + MASK_PAD) * dpr)))
        return out


def _crop(image: Img, x: int, y: int, w: int, h: int) -> bytes:
    stride = image.w * 4
    return b"".join(image.data[(y + r) * stride + x * 4:(y + r) * stride + (x + w) * 4] for r in range(h))


# ---- cat sheets -------------------------------------------------------------

def cat_sheet(state: str) -> Rendered:
    """Every coat as a row, the state's frames as columns, on a transparent
    ground. The cat is drawn by the widget itself (on_top, so nothing else
    is under it) and cropped from a grab."""
    widget = PixelWidget(T.Theme(), [Customization(cat_home="on_top")], 1)
    widget.anim.stop()
    pane = widget.panes[0]
    dpr = widget.devicePixelRatioF()
    frames = sprites.get_frames(state)
    cell = T.cell_size(dpr)
    w, h = len(frames[0][0]) * cell, len(frames[0]) * cell
    x, y = widget.cat_origin(0)
    x, y = round(x * dpr), round(y * dpr)
    pane.render(state, 42, 67, "", "", "", None, None, False)
    lines: List[bytes] = []
    for colorway, pattern in COATS:
        pane.set_appearance(palette=sprites.resolve_palette(colorway, pattern))
        cells: Dict[int, bytes] = {}
        for _ in frames:
            pane.draw_next_frame()
            _frame, index = pane.current_frame()
            cells[index] = _crop(widget.grab_img(), x, y, w, h)
        if sorted(cells) != list(range(len(frames))):
            raise RuntimeError(f"{state}: frames shown {sorted(cells)} for {len(frames)} frames")
        for row in range(h):
            lines.append(b"".join(cells[i][row * w * 4:(row + 1) * w * 4] for i in range(len(frames))))
    widget.deleteLater()
    return Rendered(Img(w * len(frames), h * len(COATS), b"".join(lines)), dpr=dpr)


# ---- layouts ----------------------------------------------------------------

def layout(name: str) -> Rendered:
    """One widget layout with fixed inputs. Tags stay empty: a tag chip would
    paint into the on_top cat's rect."""
    theme = T.Theme()
    home = "inside"
    if name == "halo":
        theme = replace(theme, text=replace(theme.text, halo="on"))
    elif name == "low_opacity":
        theme = replace(theme, card=replace(theme.card, opacity=20),
                        text=replace(theme.text, halo="auto"))
    elif name == "cat_right":
        home = "inside_right"
    elif name == "cat_top":
        home = "on_top"
    elif name not in ("default", "models"):
        raise KeyError(name)
    widget = PixelWidget(theme, [Customization(cat_home=home, label="work")], 1)
    widget.anim.stop()
    pane = widget.panes[0]
    if name == "models":
        view = UsageView(
            rows=(UsageRow("Opus 4", "$12.40", 62.0), UsageRow("Sonnet 4", "$6.10", 31.0),
                  UsageRow("Haiku", "$1.60", 8.0)),
            status_text="last 7 days", ramp_pct=None)
        pane.render_usage("working", view)
    else:
        pane.render("working", 42, 67, "resets in 2h", "Mon", "", None, "open Claude Code", False)
    img = widget.grab_img()
    out = Rendered(img, widget.mask_rects(), name in TRANSLUCENT_LAYOUTS, widget.devicePixelRatioF())
    widget.deleteLater()
    return out


# ---- names ------------------------------------------------------------------

def all_names() -> List[str]:
    names = []
    for dpr in DPRS:
        names += [f"cat/{s}@{dpr}x" for s in sprites.ALL_STATES]
        names += [f"card/{n}@{dpr}x" for n in LAYOUTS]
    return names


def split_name(name: str) -> Tuple[str, str, int]:
    kind, rest = name.split("/")
    base, scale = rest.rsplit("@", 1)
    return kind, base, int(scale.rstrip("x"))


def ref_path(name: str) -> Path:
    return REFS / f"{name}.png"


def render_name(name: str) -> Rendered:
    kind, base, _dpr = split_name(name)
    return cat_sheet(base) if kind == "cat" else layout(base)


def matches(name: str, wanted: Sequence[str]) -> bool:
    if not wanted:
        return True
    return any(name == w or name.rsplit("@", 1)[0] == w or name.startswith(w.rstrip("/") + "/")
               for w in wanted)


# ---- rendering at another dpr, in a child process ---------------------------

def dump(outdir: Path, dpr: int, wanted: Sequence[str]) -> None:
    """Render every wanted name at this process's dpr into `outdir`."""
    import PySide6
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    probe = PixelWidget(T.Theme(), [Customization()], 1)
    actual = probe.devicePixelRatioF()
    probe.deleteLater()
    if actual != dpr:
        raise SystemExit(f"asked for dpr {dpr}, this process has {actual}")
    import tokitty
    meta = {"tokitty_file": str(Path(tokitty.__file__).resolve()), "dpr": actual,
            "qt": PySide6.__version__, "platform": sys.platform}
    masks = {}
    for name in all_names():
        if split_name(name)[2] != dpr or not matches(name, wanted):
            continue
        r = render_name(name)
        write_png(outdir / f"{flat(name)}.png", r.img)
        masks[name] = {"rects": r.rects, "always_mask": r.always_mask, "dpr": r.dpr}
    (outdir / "masks.json").write_text(json.dumps(masks))
    (outdir / "meta.json").write_text(json.dumps(meta))
    del app


def render_child(dpr: int, outdir: Path, wanted: Sequence[str] = ()) -> Tuple[Dict[str, Rendered], dict]:
    """Run `dump` in a child with QT_SCALE_FACTOR=dpr and load what it wrote."""
    env = dict(os.environ, QT_SCALE_FACTOR=str(dpr), QT_QPA_PLATFORM="offscreen")
    cmd = [sys.executable, str(Path(__file__).resolve()), "dump", str(outdir), "--dpr", str(dpr), *wanted]
    proc = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"pixel_check dump at dpr {dpr} failed:\n{proc.stdout}\n{proc.stderr}")
    meta = json.loads((outdir / "meta.json").read_text())
    masks = json.loads((outdir / "masks.json").read_text())
    out = {n: Rendered(read_png(outdir / f"{flat(n)}.png"), [tuple(r) for r in m["rects"]], m["always_mask"], m["dpr"])
           for n, m in masks.items()}
    return out, meta


# ---- update -----------------------------------------------------------------

def update(wanted: Sequence[str]) -> int:
    if sys.platform == "darwin":
        print("refusing to update references on macOS: its text rendering is never the reference",
              file=sys.stderr)
        return 2
    changed = new = unchanged = 0
    pending: List[Tuple[str, Img]] = []
    for dpr in DPRS:
        if not any(split_name(n)[2] == dpr and matches(n, wanted) for n in all_names()):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            rendered, _meta = render_child(dpr, Path(tmp), wanted)
        for name in sorted(rendered):
            img = rendered[name].img
            path = ref_path(name)
            if not path.exists():
                print(f"{name}: new")
                new += 1
                pending.append((name, img))
                continue
            old = read_png(path)
            diff = compare(img, old)
            if diff is None:
                print(f"{name}: unchanged")
                unchanged += 1
                continue
            print(f"{name}: {diff.summary()}")
            changed += 1
            write_compare(diff_dir() / f"{flat(name)}.compare.png", old, img, diff)
            pending.append((name, img))
    for name, img in pending:
        write_png(ref_path(name), img, indexed=name.startswith("cat/"))
    print(f"{changed} changed, {new} new, {unchanged} unchanged")
    if changed:
        print(f"old | new | difference images in {diff_dir()}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update", help="show what changed, then write the references")
    up.add_argument("names", nargs="*", help="cat/<state>, card/<layout>, either with @<n>x, or cat / card")
    dp = sub.add_parser("dump", help="internal: render at this process's dpr")
    dp.add_argument("outdir", type=Path)
    dp.add_argument("--dpr", type=int, required=True)
    dp.add_argument("names", nargs="*")
    args = ap.parse_args(argv)
    if args.cmd == "update":
        return update(args.names)
    dump(args.outdir, args.dpr, args.names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
