"""Pixel check of the cat, after the spike's compare(): in a grab of the
widget every opaque sprite cell equals raster_rgba, every transparent cell
equals the same grab with the cat left out (the card behind it), and over
the empty margin above an on_top home the whole RGBA block matches.

Importable by the tests and runnable as a script, which is how the dpr 2
case gets its own process (a process has one QApplication, and
QT_SCALE_FACTOR must be set before it exists).
"""
import json
import sys

from PySide6.QtGui import QImage

from tokitty import sprites
from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.widget import TokittyQtWidget
from tokitty.sprite_raster import raster_rgba

COATS = (("orange", "tabby"), ("gray", "tuxedo"))


def _render(pane, state: str) -> None:
    pane.render(state, 42, 67, "2h", "Mon", "", None, None, False)


def _block(image: QImage, x: int, y: int, w: int, h: int) -> bytes:
    sub = image.copy(x, y, w, h).convertToFormat(QImage.Format.Format_RGBA8888)
    bpl = sub.bytesPerLine()
    raw = bytes(sub.constBits())
    return b"".join(raw[r * bpl: r * bpl + w * 4] for r in range(h))


def _mirror(ref: bytes, w: int, h: int) -> bytes:
    rows = [ref[r * w * 4:(r + 1) * w * 4] for r in range(h)]
    return b"".join(
        b"".join(row[i * 4:i * 4 + 4] for i in range(w - 1, -1, -1)) for row in rows)


def check(widget: TokittyQtWidget, index: int = 0) -> dict:
    widget.anim.stop()  # the frame under test must still be the one painted
    pane = widget.panes[index]
    home = widget.homes[index]
    dpr = widget.devicePixelRatioF()
    frame, _ = pane.current_frame()
    palette = pane.palette
    w, h, ref = raster_rgba(frame, palette, T.cell_size(dpr))
    mirrored = T.placement(home).mirrored
    if mirrored:
        ref = _mirror(ref, w, h)
    x, y = widget.cat_origin(index)
    ox, oy = round(x * dpr), round(y * dpr)

    with_cat = widget.grab().toImage()
    pane.set_appearance(palette={})  # an empty palette paints a fully clear cat
    without = widget.grab().toImage()
    pane.set_appearance(palette=palette)

    got = _block(with_cat, ox, oy, w, h)
    behind = _block(without, ox, oy, w, h)
    opaque = bad_opaque = bad_clear = 0
    for i in range(0, len(ref), 4):
        if ref[i + 3] == 255:
            opaque += 1
            if got[i:i + 4] != ref[i:i + 4]:
                bad_opaque += 1
        elif got[i:i + 4] != behind[i:i + 4]:
            bad_clear += 1
    return {
        "home": home, "mirrored": mirrored, "dpr": dpr, "image_dpr": with_cat.devicePixelRatio(),
        "opaque": opaque, "bad_opaque": bad_opaque, "bad_clear": bad_clear,
        # Only an empty margin lets the whole block, clear cells included, match.
        "block_equal": got == ref if home == "on_top" else None,
    }


def run_all() -> list:
    results = []
    for colorway, pattern in COATS:
        for home in T.SPOTS:
            custom = Customization(colorway=colorway, pattern=pattern, cat_home=home)
            widget = TokittyQtWidget(T.Theme(), [custom], 1)
            states = sprites.ALL_STATES if home.startswith("inside") else sprites.ALL_STATES[:3]
            for state in states:
                _render(widget.panes[0], state)
                results.append(check(widget))
                widget.panes[0].draw_next_frame()  # frame 0 shown; advance
                widget.panes[0].draw_next_frame()  # and a second frame
                results.append(check(widget))
                widget.panes[0]._player.restart(state, 0)
                widget.panes[0].draw_next_frame()
            widget.deleteLater()
    return results


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    print(json.dumps(run_all()))
    sys.exit(0)
