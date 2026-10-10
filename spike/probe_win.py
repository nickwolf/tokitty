"""Windows-only probe for the spike: hit testing, focus, stacking, drag.

Hit testing uses WindowFromPoint resolved through GetAncestor(GA_ROOT), the
method #37 used. Real clicks go only to pixels tokitty owns (cat, card, bar),
never to the empty margin, so nothing underneath is ever clicked.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import time
from pathlib import Path

from PySide6.QtCore import QRect, QTimer
from PySide6.QtWidgets import QApplication, QWidget

import qt_card as qc
from tokitty.sprite_raster import raster_rgba

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
GA_ROOT = 2
GWL_EXSTYLE = -20
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x2, 0x4
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x8, 0x10
EX_BITS = {"WS_EX_TOPMOST": 0x8, "WS_EX_TRANSPARENT": 0x20, "WS_EX_TOOLWINDOW": 0x80,
           "WS_EX_APPWINDOW": 0x40000, "WS_EX_LAYERED": 0x80000, "WS_EX_NOACTIVATE": 0x8000000}


def settle(app, ms=250):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def describe(hwnd) -> str:
    if not hwnd:
        return "NULL"
    t = ctypes.create_unicode_buffer(256)
    c = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, t, 256)
    user32.GetClassNameW(hwnd, c, 256)
    return f"{hwnd:#x} [{c.value}] {t.value[:60]!r}"


def win_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def root_at(x, y):
    return user32.GetAncestor(user32.WindowFromPoint(wt.POINT(x, y)), GA_ROOT)


def click(x, y, right=False):
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    d, u = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if right else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    user32.mouse_event(d, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(u, 0, 0, 0, 0)


def run_probe(app, card, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    hwnd = int(card.winId())
    rep = {"hwnd": describe(hwnd)}
    fg0 = user32.GetForegroundWindow()
    rep["foreground_at_start"] = describe(fg0)
    cur0 = wt.POINT()
    user32.GetCursorPos(ctypes.byref(cur0))
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    rep["exstyle"] = {k: bool(ex & v) for k, v in EX_BITS.items()}
    rep["exstyle_hex"] = hex(ex)
    rep["GetDpiForWindow"] = user32.GetDpiForWindow(hwnd)
    rep["qt_dpr"] = card.dpr()

    d = card.dpr()

    def phys(lx, ly):
        left, top, _, _ = win_rect(hwnd)
        return left + round(lx * d), top + round(ly * d)

    def opaque_cat_point():
        w, h, ref = raster_rgba(card.frames[card.frame_i], card.palette_, qc.cell_for(d))
        ox, oy = card.cat_device_origin()
        left, top, _, _ = win_rect(hwnd)
        # middle-most opaque pixel
        best = None
        for i in range(0, len(ref), 4):
            if ref[i + 3] == 255:
                px, py = (i // 4) % w, (i // 4) // w
                score = abs(px - w / 2) + abs(py - h / 2)
                if best is None or score < best[0]:
                    best = (score, px, py)
        return left + ox + best[1], top + oy + best[2]

    def clear_cat_point():
        w, h, ref = raster_rgba(card.frames[card.frame_i], card.palette_, qc.cell_for(d))
        ox, oy = card.cat_device_origin()
        left, top, _, _ = win_rect(hwnd)
        assert ref[3] == 0  # sprite's top-left cell is empty
        return left + ox + 1, top + oy + 1

    hits = {}
    card.set_spot("inside")
    card.set_opacity(100)
    settle(app, 400)
    probes = {
        "margin_top_right": phys(qc.WIN_W - 6, 6),
        "margin_left": phys(4, qc.CARD.top() + 60),
        "card_empty_area": phys(qc.CARD.left() + 200, qc.CARD.top() + 112),
        "bar_fill": phys(qc.CARD.left() + qc.STATS_X + 10, qc.CARD.top() + 24 + 4),
        "card_rounded_corner_outside": phys(qc.CARD.left() + 1, qc.CARD.top() + 1),
    }
    for o in (100, 50, 20, 1, 0):
        if o == 1:
            card.set_opacity(0)
            card.floor = True
        else:
            card.floor = False
            card.set_opacity(o)
        card.repaint()
        settle(app, 300)
        label = "0_floor_alpha1" if o == 1 else str(o)
        hits[label] = {name: root_at(*pt) == hwnd for name, pt in probes.items()}
        hits[label]["cat_opaque_pixel"] = root_at(*opaque_cat_point()) == hwnd
    card.floor = False
    card.set_opacity(100)
    card.set_spot("beside")
    card.repaint()
    settle(app, 300)
    hits["beside_cat_opaque_pixel"] = root_at(*opaque_cat_point()) == hwnd
    hits["beside_cat_transparent_cell"] = root_at(*clear_cat_point()) == hwnd
    hits["beside_what_is_under_transparent_cell"] = describe(root_at(*clear_cat_point()))
    card.set_spot("on_top")
    card.repaint()
    settle(app, 300)
    hits["on_top_cat_opaque_pixel"] = root_at(*opaque_cat_point()) == hwnd
    rep["hit_test_true_means_tokitty"] = hits

    # ---- focus: real clicks on the cat and the card --------------------
    focus = {}
    card.set_spot("inside")
    card.repaint()
    settle(app, 300)
    card.last_press = None
    click(*opaque_cat_point())
    settle(app, 400)
    focus["left_click_cat_received"] = card.last_press is not None
    focus["foreground_after_click_cat"] = describe(user32.GetForegroundWindow())
    focus["foreground_unchanged_after_click_cat"] = user32.GetForegroundWindow() == fg0
    focus["qt_isActiveWindow"] = card.isActiveWindow()

    card.last_press = None
    click(*probes["card_empty_area"])
    settle(app, 400)
    focus["left_click_card_received"] = card.last_press is not None
    focus["foreground_unchanged_after_click_card"] = user32.GetForegroundWindow() == fg0

    # right-click opens the QMenu (nested loop); inspect, then close it
    menu_state = {}

    def inspect_menu():
        pop = QApplication.activePopupWidget()
        menu_state["popup_open"] = pop is not None
        menu_state["foreground_while_menu_open"] = describe(user32.GetForegroundWindow())
        menu_state["foreground_unchanged_while_menu_open"] = user32.GetForegroundWindow() == fg0
        if pop:
            pop.close()

    QTimer.singleShot(700, inspect_menu)
    click(*probes["card_empty_area"], right=True)
    settle(app, 1200)
    menu_state["foreground_unchanged_after_menu_closed"] = user32.GetForegroundWindow() == fg0
    focus["context_menu"] = menu_state
    rep["focus"] = focus

    # ---- drag ------------------------------------------------------------
    drag = {}
    r0 = win_rect(hwnd)
    sx, sy = opaque_cat_point()
    user32.SetCursorPos(sx, sy)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    settle(app, 100)
    for i in range(1, 21):
        user32.SetCursorPos(sx + 6 * i, sy + 4 * i)
        settle(app, 20)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    settle(app, 300)
    r1 = win_rect(hwnd)
    drag["moved_px"] = [r1[0] - r0[0], r1[1] - r0[1]]
    drag["expected_px"] = [120, 80]
    drag["foreground_unchanged_after_drag"] = user32.GetForegroundWindow() == fg0
    rep["drag"] = drag

    # ---- stacking: a normal window activated over the widget -------------
    stack = {}
    other = QWidget()
    other.setWindowTitle("spike normal window")
    left, top, right, bottom = win_rect(hwnd)
    other.setGeometry(QRect(round((left - 50) / d), round((top - 50) / d),
                            round((right - left + 100) / d), round((bottom - top + 100) / d)))
    other.show()
    other.raise_()
    other.activateWindow()
    settle(app, 500)
    user32.SetForegroundWindow(int(other.winId()))
    settle(app, 500)
    stack["normal_window_is_foreground"] = user32.GetForegroundWindow() == int(other.winId())
    stack["widget_still_on_top_at_cat"] = root_at(*opaque_cat_point()) == hwnd
    stack["margin_now_hits_normal_window"] = root_at(*probes["margin_top_right"]) == int(other.winId())
    # click the widget while another of our windows is foreground
    click(*opaque_cat_point())
    settle(app, 400)
    stack["foreground_after_click_while_normal_window_active"] = describe(user32.GetForegroundWindow())
    other.close()
    settle(app, 200)
    rep["stacking"] = stack

    user32.SetForegroundWindow(fg0)
    user32.SetCursorPos(cur0.x, cur0.y)
    settle(app, 200)
    rep["foreground_restored"] = user32.GetForegroundWindow() == fg0
    (out / "probe_win32.json").write_text(json.dumps(rep, indent=2))
    return rep
