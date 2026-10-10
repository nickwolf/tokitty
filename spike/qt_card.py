"""PySide6 go/no-go spike for #28. Throwaway: nothing here is merged into the app.

One frameless, always-on-top, per-pixel-alpha window larger than the card, so
the cat can sit inside the card, on its top edge, beside it, or walk along the
top edge. Imports sprites and palettes from the tokitty package and nothing
else from it.

Run from the repo root:
    python spike/qt_card.py              interactive
    python spike/qt_card.py --walk       start with the cat walking (CPU gate)
    python spike/qt_card.py --selftest   pixel identity + text screenshots, then quit
    python spike/qt_card.py --probe      Windows-only hit-test/focus/stacking probe, then quit

All controls are on the right-click menu and the tray menu, because the window
never takes keyboard focus. The mouse wheel over the card changes its opacity.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction, QActionGroup, QColor, QCursor, QFont, QGuiApplication, QIcon,
    QImage, QPainter, QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget  # noqa: E402

from tokitty.sprite_raster import raster_rgba  # noqa: E402
from tokitty.sprites import SCALE, get_frames, get_palette  # noqa: E402

FRAME_INTERVAL_MS = 800
WALK_INTERVAL_MS = 33  # ~30 fps
WALK_STEP = 2  # logical px per walk tick

# Logical-pixel layout, copied from tokitty/ui.py so the card matches the app.
CARD_W, CARD_H = 300, 128
BAR_W, BAR_H = 158, 8
STATS_X = 132
BG = QColor("#1c1c22")
FG = QColor("#f0f0f0")
DIM = QColor("#8a8a92")
BAR_BG = QColor("#333340")
SESSION_COLOR = QColor("#4caf50")
WEEKLY_COLOR = QColor("#e8a33c")
SESSION_PCT, WEEKLY_PCT = 42, 67

SPRITE_COLS, SPRITE_ROWS = 28, 26
CAT_W, CAT_H = SPRITE_COLS * SCALE, SPRITE_ROWS * SCALE  # 112 x 104 logical

# The window extends past the card on every side the cat can reach. All
# margins are multiples of 4 so the card origin lands on a whole device pixel
# at 125%, 150% and 175% scaling.
M_LEFT, M_TOP, M_RIGHT, M_BOTTOM = 16, CAT_H + 8, CAT_W + 16, 16
WIN_W = M_LEFT + CARD_W + M_RIGHT
WIN_H = M_TOP + CARD_H + M_BOTTOM
CARD = QRect(M_LEFT, M_TOP, CARD_W, CARD_H)

SPOTS = ("inside", "on_top", "beside", "walk")
STATES = ("idle", "working", "thinking", "sleeping", "permission")
OPACITIES = (100, 80, 50, 20, 0)


def pick_state(name: str) -> str:
    """The spike's state names map onto whatever the sprite module calls them."""
    from tokitty import sprites
    candidates = {
        "idle": ("content", "idle", "calm"),
        "working": ("working", "typing"),
        "thinking": ("thinking",),
        "sleeping": ("sleeping", "asleep", "sleepy"),
        "permission": ("permission", "waiting"),
    }[name]
    for c in candidates:
        if c in sprites.ALL_STATES:
            return c
    for s in sprites.ALL_STATES:
        if any(c in s for c in candidates):
            return s
    return sprites.ALL_STATES[0]


def cell_for(dpr: float) -> int:
    """Device pixels per sprite pixel at this device pixel ratio. Every Windows
    scaling step (100-250% in 25% steps) gives a whole number here."""
    return round(SCALE * dpr)


def cat_image(frame, palette, dpr: float, mirrored: bool = False) -> QImage:
    w, h, data = raster_rgba(frame, palette, cell_for(dpr))
    img = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    if mirrored:
        img = img.flipped(Qt.Orientation.Horizontal) if hasattr(img, "flipped") else img.mirrored(True, False)
    img.setDevicePixelRatio(dpr)
    return img


class Card(QWidget):
    def __init__(self, coat: str = "orange_tabby", walk: bool = False):
        super().__init__()
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar button
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFixedSize(WIN_W, WIN_H)

        self.palette_ = get_palette(coat)
        self.state = pick_state("idle")
        self.frames = get_frames(self.state)
        self.frame_i = 0
        self.spot = "walk" if walk else "inside"
        self.opacity = 100
        self.floor = False  # paint a 0% card at alpha 1/255 so it stays clickable
        self.halo = False  # 1px dark outline around text, for low card opacity
        self.walk_x = 0.0
        self.walk_dir = 1
        self.show_cat = True
        self._cache = {}
        self._drag = None
        self.paints = 0
        self.walk_ticks = 0
        self.frame_ticks = 0

        self.anim = QTimer(self)
        self.anim.timeout.connect(self._next_frame)
        self.anim.start(FRAME_INTERVAL_MS)
        self.walker = QTimer(self)
        self.walker.setTimerType(Qt.TimerType.PreciseTimer)
        self.walker.timeout.connect(self._walk_tick)
        if walk:
            self.walker.start(WALK_INTERVAL_MS)

    # ---- geometry -------------------------------------------------------
    def dpr(self) -> float:
        return self.devicePixelRatioF()

    def snap(self, v: float) -> float:
        d = self.dpr()
        return round(v * d) / d

    def cat_pos(self) -> QPointF:
        if self.spot == "inside":
            x, y = CARD.left() + 10, CARD.top() + (CARD_H - CAT_H) // 2
        elif self.spot == "on_top":
            x, y = CARD.left() + 24, CARD.top() - CAT_H + 4
        elif self.spot == "beside":
            x, y = CARD.right() + 1 + 8, CARD.bottom() + 1 - CAT_H
        else:
            x, y = CARD.left() + self.walk_x, CARD.top() - CAT_H + 4
        return QPointF(self.snap(x), self.snap(y))

    def cat_rect(self) -> QRectF:
        return QRectF(self.cat_pos(), QPointF(self.cat_pos().x() + CAT_W, self.cat_pos().y() + CAT_H))

    def cat_device_origin(self):
        p = self.cat_pos()
        d = self.dpr()
        return round(p.x() * d), round(p.y() * d)

    def mirrored(self) -> bool:
        return self.spot == "walk" and self.walk_dir < 0

    def current_image(self) -> QImage:
        key = (self.state, self.frame_i, self.dpr(), self.mirrored())
        img = self._cache.get(key)
        if img is None:
            img = cat_image(self.frames[self.frame_i], self.palette_, self.dpr(), self.mirrored())
            self._cache[key] = img
        return img

    # ---- timers ---------------------------------------------------------
    def _next_frame(self):
        self.frame_ticks += 1
        self.frame_i = (self.frame_i + 1) % len(self.frames)
        self.update()

    def _walk_tick(self):
        self.walk_ticks += 1
        span = CARD_W - CAT_W
        self.walk_x += WALK_STEP * self.walk_dir
        if self.walk_x >= span:
            self.walk_x, self.walk_dir = span, -1
        elif self.walk_x <= 0:
            self.walk_x, self.walk_dir = 0, 1
        self.update()

    def set_spot(self, spot: str):
        self.spot = spot
        if spot == "walk":
            self.walker.start(WALK_INTERVAL_MS)
        else:
            self.walker.stop()
        self.update()

    def set_state(self, name: str):
        self.state = pick_state(name)
        self.frames = get_frames(self.state)
        self.frame_i = 0
        self.update()

    def set_opacity(self, pct: int):
        self.opacity = max(0, min(100, pct))
        self.update()

    # ---- painting -------------------------------------------------------
    def text(self, p: QPainter, color: QColor, where, s: str, flags=None):
        draw = (lambda: p.drawText(where, flags, s)) if flags is not None else (lambda: p.drawText(where, s))
        if self.halo:
            p.setPen(QColor(0, 0, 0, 200))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
                p.translate(dx, dy)
                draw()
                p.translate(-dx, -dy)
        p.setPen(color)
        draw()

    def paintEvent(self, _event):
        self.paints += 1
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        alpha = round(255 * self.opacity / 100)
        if alpha == 0 and self.floor:
            alpha = 1
        if alpha:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            c = QColor(BG)
            c.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(CARD), 10, 10)

        # Bars and labels stay fully opaque whatever the card does.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        bx = CARD.left() + STATS_X
        rows = (("SESSION", SESSION_PCT, SESSION_COLOR, "resets in 2h 14m", 24),
                ("WEEK", WEEKLY_PCT, WEEKLY_COLOR, "resets Mon 18:00", 72))
        bold = QFont(self.font())
        bold.setPointSize(9)
        bold.setBold(True)
        small = QFont(self.font())
        small.setPointSize(8)
        for label, pct, color, reset, y in rows:
            y0 = CARD.top() + y
            p.setFont(bold)
            self.text(p, FG, QPointF(bx, y0 - 4), label)
            self.text(p, FG, QRectF(bx, y0 - 18, BAR_W, 16), f"{pct}%",
                      Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            p.fillRect(QRectF(bx, y0, BAR_W, BAR_H), BAR_BG)
            p.fillRect(QRectF(bx, y0, BAR_W * pct / 100, BAR_H), color)
            p.setFont(small)
            self.text(p, DIM, QPointF(bx, y0 + BAR_H + 13), reset)

        if self.show_cat:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(self.cat_pos(), self.current_image())
        p.end()

    # ---- input ----------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.last_press = ("left", time.time())
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu().exec(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def wheelEvent(self, e):
        self.set_opacity(self.opacity + (10 if e.angleDelta().y() > 0 else -10))

    def menu(self) -> QMenu:
        m = QMenu(self)
        build_menu(m, self)
        return m


def build_menu(m: QMenu, card: Card):
    pos = m.addMenu("Cat position")
    g = QActionGroup(pos)
    for s in SPOTS:
        a = pos.addAction(s.replace("_", " "))
        a.setCheckable(True)
        a.setChecked(card.spot == s)
        a.triggered.connect(lambda _=False, s=s: card.set_spot(s))
        g.addAction(a)
    op = m.addMenu("Card opacity")
    g2 = QActionGroup(op)
    for o in OPACITIES:
        a = op.addAction(f"{o}%")
        a.setCheckable(True)
        a.setChecked(card.opacity == o)
        a.triggered.connect(lambda _=False, o=o: card.set_opacity(o))
        g2.addAction(a)
    fl = m.addAction("Keep 0% card clickable (alpha 1/255)")
    fl.setCheckable(True)
    fl.setChecked(card.floor)
    fl.triggered.connect(lambda v: (setattr(card, "floor", v), card.update()))
    ha = m.addAction("Text halo")
    ha.setCheckable(True)
    ha.setChecked(card.halo)
    ha.triggered.connect(lambda v: (setattr(card, "halo", v), card.update()))
    st = m.addMenu("Cat state")
    for s in STATES:
        st.addAction(s).triggered.connect(lambda _=False, s=s: card.set_state(s))
    m.addSeparator()
    m.addAction("Quit").triggered.connect(QApplication.quit)


def make_tray(app: QApplication, card: Card) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("tray: QSystemTrayIcon.isSystemTrayAvailable() is False")
        return None
    icon = QIcon(QPixmap.fromImage(cat_image(get_frames(pick_state("idle"))[0], card.palette_, 1.0)))
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
    menu.aboutToShow.connect(lambda: (menu.clear(), build_menu(menu, card)))
    tray.setContextMenu(menu)
    tray.setToolTip("tokitty Qt spike")
    tray.show()
    tray._menu = menu  # keep a reference
    return tray


# ---- self-test: pixel identity and text screenshots -------------------------
def region_bytes(img: QImage, x: int, y: int, w: int, h: int) -> bytes:
    sub = img.copy(x, y, w, h).convertToFormat(QImage.Format.Format_RGBA8888)
    bpl = sub.bytesPerLine()
    raw = bytes(sub.constBits())
    return b"".join(raw[r * bpl: r * bpl + w * 4] for r in range(h))


def compare(card: Card, source: str) -> dict:
    """Compare the cat as drawn against raster_rgba, opaque cells only, plus the
    transparent cells against the same scene with the cat hidden."""
    dpr = card.dpr()
    frame = card.frames[card.frame_i]
    w, h, ref = raster_rgba(frame, card.palette_, cell_for(dpr))
    if card.mirrored():
        rows = [ref[r * w * 4:(r + 1) * w * 4] for r in range(h)]
        ref = b"".join(b"".join(row[i * 4:i * 4 + 4] for i in range(w - 1, -1, -1)) for row in rows)
    ox, oy = card.cat_device_origin()

    def shot() -> QImage:
        if source == "grab":
            return card.grab().toImage()
        # macOS composites asynchronously, so give the last repaint time to
        # reach the screen before capturing it. Windows doesn't need this.
        end = time.time() + 0.3
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        scr = card.screen()
        geo = card.geometry()
        pm = scr.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        return pm.toImage()

    with_cat = shot()
    card.show_cat = False
    card.repaint()
    QApplication.processEvents()
    without = shot()
    card.show_cat = True
    card.repaint()
    QApplication.processEvents()

    got = region_bytes(with_cat, ox, oy, w, h)
    bg = region_bytes(without, ox, oy, w, h)
    bad_opaque = bad_clear = opaque = 0
    for i in range(0, len(ref), 4):
        if ref[i + 3] == 255:
            opaque += 1
            if got[i:i + 3] != ref[i:i + 3] or (source == "grab" and got[i + 3] != 255):
                bad_opaque += 1
        elif got[i:i + 4] != bg[i:i + 4]:
            bad_clear += 1
    exact_rgba = None
    if card.spot == "beside" and source == "grab":
        exact_rgba = got == ref  # over empty margin the whole RGBA block must match
    return {
        "source": source, "spot": card.spot, "mirrored": card.mirrored(), "dpr": dpr,
        "cell": cell_for(dpr), "image_px": [with_cat.width(), with_cat.height()],
        "cat_device_origin": [ox, oy], "opaque_cells_px": opaque,
        "opaque_mismatch_px": bad_opaque, "transparent_mismatch_px": bad_clear,
        "exact_rgba_block_match": exact_rgba,
    }


class Backdrop(QWidget):
    """A plain, normal window behind the card: light and dark halves plus busy
    text, so screenshots show the labels over both."""

    def __init__(self, geo: QRect):
        super().__init__()
        # Topmost too, so no other app's window (and its content) can sit
        # between it and the card in a screenshot. The card is raised after.
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(geo)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(QRect(0, 0, r.width() // 2, r.height()), QColor("#f4f4f0"))
        p.fillRect(QRect(r.width() // 2, 0, r.width() - r.width() // 2, r.height()), QColor("#2a3a55"))
        p.setPen(QColor("#888888"))
        f = QFont(self.font())
        f.setPointSize(10)
        p.setFont(f)
        for y in range(14, r.height(), 18):
            p.drawText(4, y, "def tokitty(): return usage.session_pct  # lorem ipsum dolor sit amet " * 2)
        p.end()


def run_selftest(app: QApplication, card: Card, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report = {"platform": sys.platform, "qt": __import__("PySide6").__version__,
              "screens": [{"name": s.name(), "dpr": s.devicePixelRatio(),
                           "geometry": [s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height()]}
                          for s in QGuiApplication.screens()],
              "pixel": [], "shots": []}
    geo = card.geometry()
    backdrop = Backdrop(geo.adjusted(-40, -40, 40, 40))
    backdrop.show()
    card.hide()
    card.show()
    card.raise_()

    def settle(ms=250):
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    settle(600)
    for spot in ("inside", "on_top", "beside", "walk"):
        card.set_spot(spot)
        card.walker.stop()
        if spot == "walk":
            for direction in (1, -1):
                card.walk_dir = direction
                card.walk_x = 60
                card.repaint()
                settle()
                report["pixel"].append(compare(card, "grab"))
                report["pixel"].append(compare(card, "screen"))
            continue
        card.repaint()
        settle()
        report["pixel"].append(compare(card, "grab"))
        report["pixel"].append(compare(card, "screen"))

    card.set_spot("inside")
    for halo in (False, True):
        card.halo = halo
        for o in (100, 50, 20, 0):
            card.set_opacity(o)
            card.repaint()
            settle(400)
            g = card.geometry().adjusted(-24, -24, 24, 24)
            pm = card.screen().grabWindow(0, g.x(), g.y(), g.width(), g.height())
            name = f"text_{sys.platform}_{o:03d}{'_halo' if halo else ''}.png"
            pm.save(str(out / name))
            report["shots"].append(name)
    card.halo = False
    card.set_opacity(100)
    backdrop.close()
    (out / f"selftest_{sys.platform}.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--walk", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "spike" / "out"))
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--pos", default=None, help="x,y in logical px")
    ap.add_argument("--quit-after", type=float, default=0, help="seconds")
    ap.add_argument("--cpu", type=float, default=0, help="measure own CPU over N s after 5 s warmup, then quit")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    card = Card(walk=args.walk)
    if args.pos:
        x, y = (int(v) for v in args.pos.split(","))
        card.move(x, y)
    card.show()
    tray = None if args.no_tray else make_tray(app, card)
    print(json.dumps({"pid": os.getpid(), "dpr": card.dpr(), "tray": tray is not None,
                      "state": card.state, "win": [WIN_W, WIN_H]}), flush=True)

    app.aboutToQuit.connect(lambda: print(json.dumps({"paints": card.paints, "walk_ticks": card.walk_ticks,
                                                      "frame_ticks": card.frame_ticks}), flush=True))
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after * 1000), app.quit)
    if args.cpu:
        mark = {}

        def start():
            mark.update(t=time.perf_counter(), c=time.process_time(), p=card.paints)

        def stop():
            wall = time.perf_counter() - mark["t"]
            cpu = time.process_time() - mark["c"]
            print(json.dumps({"cpu_window_s": round(wall, 2), "cpu_s": round(cpu, 3),
                              "pct_of_one_core": round(100 * cpu / wall, 3),
                              "paints_per_s": round((card.paints - mark["p"]) / wall, 1),
                              "walk": args.walk}), flush=True)
            app.quit()
        QTimer.singleShot(5000, start)
        QTimer.singleShot(int((5 + args.cpu) * 1000), stop)
    if args.selftest:
        def go():
            r = run_selftest(app, card, Path(args.out))
            print(json.dumps(r, indent=2), flush=True)
            app.quit()
        QTimer.singleShot(500, go)
    if args.probe:
        from probe_win import run_probe  # spike/probe_win.py, Windows only
        def go_probe():
            r = run_probe(app, card, Path(args.out))
            print(json.dumps(r, indent=2), flush=True)
            app.quit()
        QTimer.singleShot(500, go_probe)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
