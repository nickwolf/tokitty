"""Mock-ups of the #211 patterns on the sitting pose, painted cell by cell.

Run from the repo root: PYTHONPATH=. python mock_patterns.py out.png
Needs Pillow. The masks repaint single cells; the real patterns will need
new region chars in the templates, since a pattern maps a whole char to one tone.
"""
import sys

from PIL import Image, ImageDraw, ImageFont

from tokitty import sprites
from tokitty.sprites import SITTING_TEMPLATE, _apply

# The belly region starts as coat; each design paints its own white cells.
pose = [r.replace("u", "o") for r in _apply(SITTING_TEMPLATE, {"L": "e", "R": "e", "A": "n"})]
BODY = set("oOscxmuyY")


def white(cells):
    g = [list(r) for r in pose]
    for r, c in cells:
        if g[r][c] in BODY:
            g[r][c] = "u"
    return ["".join(r) for r in g]


def box(r0, r1, c0, c1):
    return {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}


def rows(spec):
    return {(r, c) for r, spans in spec.items() for a, b in spans for c in range(a, b + 1)}


ALL = box(0, 25, 0, 27)
TAIL = {(r, c) for r, row in enumerate(pose) for c, ch in enumerate(row) if ch in "yY"}
EARS = box(0, 4, 0, 27)
CAP = box(0, 7, 0, 27) | rows({8: [(0, 10), (16, 27)]})
SOFT_CAP = box(0, 6, 0, 27) | rows({7: [(0, 11), (15, 27)], 8: [(0, 10), (16, 27)], 9: [(0, 8), (18, 27)]})
NOTCH = rows({5: [(13, 13)], 6: [(13, 13)], 7: [(12, 14)]})
SIDES = box(14, 23, 0, 8) | box(14, 23, 18, 27)
SADDLE = rows({17: [(17, 20)], 18: [(16, 21)], 19: [(15, 21)], 20: [(15, 21)],
               21: [(15, 21)], 22: [(16, 21)], 23: [(17, 20)]})
SPOTS = box(15, 17, 6, 9) | box(19, 21, 15, 18) | box(5, 6, 15, 18)
BIB = rows({13: [(11, 15)], 14: [(11, 15)], 15: [(10, 16)], 16: [(10, 16)], 17: [(10, 16)]})
BELLY = box(18, 23, 10, 16)
PAWS = box(24, 25, 0, 27)

DESIGNS = [
    ("Tuxedo", white(BIB | BELLY | PAWS)),
    ("Bicolor", white((ALL - CAP - TAIL - SIDES) | NOTCH)),
    ("Harlequin", white(ALL - EARS - TAIL - SPOTS)),
    ("Cap & saddle", white(ALL - SOFT_CAP - TAIL - SADDLE)),
]
COLORWAYS = ["black", "orange", "gray", "blue", "seal"]
sprites.PATTERNS["_mock"] = {"o": "coat", "O": "shade", "s": "coat", "c": "coat", "m": "coat",
                             "x": "coat", "y": "coat", "u": "white", "Y": "coat"}


def main(out):
    s, gap, left, top = 7, 22, 90, 44
    cell_w, cell_h = len(pose[0]) * s, len(pose) * s
    img = Image.new("RGB", (left + len(DESIGNS) * (cell_w + gap), top + len(COLORWAYS) * (cell_h + gap)), "#1b1b22")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("DejaVuSans.ttf", 16)
    for i, (label, p) in enumerate(DESIGNS):
        x0 = left + i * (cell_w + gap)
        d.text((x0 + cell_w / 2, top / 2), label, fill="#dddddd", font=font, anchor="mm")
        for j, cw in enumerate(COLORWAYS):
            y0 = top + j * (cell_h + gap)
            if i == 0:
                d.text((8, y0 + cell_h / 2), cw, fill="#dddddd", font=font, anchor="lm")
            pal = sprites.resolve_palette(cw, "_mock")
            for ry, row in enumerate(p):
                for cx, ch in enumerate(row):
                    if pal.get(ch):
                        d.rectangle([x0 + cx * s, y0 + ry * s, x0 + cx * s + s - 1, y0 + ry * s + s - 1],
                                    fill=pal[ch])
    img.save(out)


if __name__ == "__main__":
    main(sys.argv[1])
