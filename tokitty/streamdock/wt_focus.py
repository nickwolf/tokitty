"""Bring a Claude Code session's Windows Terminal tab forward, and press Esc in it.

A Claude Code tab is named "<status glyph> <AI title>", and the AI title is the
last `ai-title` line in the session transcript. Focusing finds the one tab whose
name matches that title across all Windows Terminal windows, selects it and
raises its window. Interrupt only sends Esc after re-checking that the tab is
still the selected one in the foreground window, because Esc in the wrong tab
can dismiss a different session's prompt.

UIA calls can take hundreds of milliseconds, so everything here is meant to run
on the single FocusWorker thread, never on the Tk thread or the HTTP thread.
The real UIA adapter is built lazily and only on Windows with comtypes, so this
module imports cleanly everywhere. Tests use a fake adapter.
"""
from __future__ import annotations

import glob
import json
import os
import queue
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from tokitty.streamdock.model import SessionRef

FOCUSED = "focused"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"
NO_TITLE = "no_title"
UNAVAILABLE = "unavailable"
NOT_FRONT = "not_front"

TAIL_BYTES = 256 * 1024
WT_WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"


@dataclass(frozen=True)
class Tab:
    window_handle: int
    name: str
    selected: bool
    element: Any = None


class UiaAdapter(Protocol):
    def list_tabs(self) -> List[Tab]: ...
    def select(self, tab: Tab) -> None: ...
    def bring_to_front(self, window_handle: int) -> bool: ...
    def foreground_window(self) -> int: ...
    def send_escape_key(self) -> None: ...


GlobFn = Callable[[str], List[str]]
ReadTailFn = Callable[[str], Optional[str]]


def read_tail(path: str, nbytes: int = TAIL_BYTES) -> Optional[str]:
    """The last `nbytes` of a file as text, or None if it cannot be read.

    When the read starts mid-file the first line is cut off, so it is dropped.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - nbytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    if start > 0:
        nl = text.find("\n")
        text = "" if nl < 0 else text[nl + 1:]
    return text


def title_from_tail(text: str, session_id: str) -> Optional[str]:
    """The last ai-title in `text` whose sessionId matches or is absent."""
    title: Optional[str] = None
    for line in text.splitlines():
        if '"ai-title"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "ai-title":
            continue
        sid = entry.get("sessionId")
        if sid and sid != session_id:
            continue
        value = entry.get("aiTitle")
        if isinstance(value, str) and value.strip():
            title = value.strip()
    return title


def normalise_tab_name(name: str) -> str:
    """Drop a leading non-alphanumeric glyph and the whitespace after it."""
    s = name.strip()
    if s and not s[0].isalnum():
        s = s[1:].lstrip()
    return s


def tab_matches(name: str, title: str) -> bool:
    return normalise_tab_name(name) == title or name.strip() == title


class WtFocus:
    """Focus and interrupt logic over a UIA adapter. Not thread safe."""

    def __init__(
        self,
        adapter: Optional[UiaAdapter],
        glob_fn: GlobFn = glob.glob,
        read_tail_fn: ReadTailFn = read_tail,
    ) -> None:
        self._adapter = adapter
        self._glob = glob_fn
        self._read_tail = read_tail_fn
        self._paths: Dict[SessionRef, str] = {}
        self._matched: Dict[SessionRef, Tuple[int, str]] = {}

    @property
    def available(self) -> bool:
        return self._adapter is not None

    def forget(self, session: SessionRef) -> None:
        self._paths.pop(session, None)
        self._matched.pop(session, None)

    def _transcript(self, session: SessionRef, config_dir: str) -> Optional[str]:
        cached = self._paths.get(session)
        if cached is not None and os.path.exists(cached):
            return cached
        pattern = os.path.join(
            glob.escape(config_dir), "projects", "*", glob.escape(session.session_id) + ".jsonl"
        )
        hits = sorted(self._glob(pattern))
        if not hits:
            self._paths.pop(session, None)
            return None
        self._paths[session] = hits[0]
        return hits[0]

    def title(self, session: SessionRef, config_dir: str) -> Optional[str]:
        path = self._transcript(session, config_dir)
        if path is None:
            return None
        text = self._read_tail(path)
        if text is None:
            return None
        return title_from_tail(text, session.session_id)

    def focus(self, session: SessionRef, config_dir: str) -> str:
        if self._adapter is None:
            return UNAVAILABLE
        title = self.title(session, config_dir)
        if title is None:
            self._matched.pop(session, None)
            return NO_TITLE
        matches = [t for t in self._adapter.list_tabs() if tab_matches(t.name, title)]
        if not matches:
            self._matched.pop(session, None)
            return NOT_FOUND
        if len(matches) > 1:
            self._matched.pop(session, None)
            return AMBIGUOUS
        tab = matches[0]
        self._adapter.select(tab)
        if not self._adapter.bring_to_front(tab.window_handle):
            self._matched.pop(session, None)
            return NOT_FRONT
        self._matched[session] = (tab.window_handle, title)
        return FOCUSED

    def is_selected(self, session: SessionRef) -> bool:
        """True only if the remembered tab is unique, selected and in the foreground window."""
        if self._adapter is None:
            return False
        remembered = self._matched.get(session)
        if remembered is None:
            return False
        handle, title = remembered
        matches = [t for t in self._adapter.list_tabs() if tab_matches(t.name, title)]
        if len(matches) != 1:
            return False
        tab = matches[0]
        return (
            tab.window_handle == handle
            and tab.selected
            and self._adapter.foreground_window() == handle
        )

    def send_escape(self, session: SessionRef) -> bool:
        if not self.is_selected(session):
            return False
        assert self._adapter is not None
        self._adapter.send_escape_key()
        return True


# Worker results, delivered to on_result on the worker thread.

@dataclass(frozen=True)
class FocusDone:
    session: SessionRef
    seq: int
    status: str


@dataclass(frozen=True)
class VerifyDone:
    nonce: str
    ok: bool


@dataclass(frozen=True)
class InterruptDone:
    session: SessionRef
    sent: bool


@dataclass(frozen=True)
class TitleDone:
    session: SessionRef
    title: Optional[str]


_STOP = object()
_Job = Tuple[Callable[[WtFocus], Any], Any]


class FocusWorker:
    """One daemon thread that owns the WtFocus and runs its jobs in order.

    Each job carries the safe result to report if it raises, so a failure never
    kills the thread and the caller still hears back.
    """

    def __init__(self, make_focus: Callable[[], WtFocus], on_result: Callable[[Any], None]) -> None:
        self._make_focus = make_focus
        self._on_result = on_result
        self._jobs: "queue.Queue[Any]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="streamdock-focus", daemon=True)
        self._thread.start()

    def submit_focus(self, session: SessionRef, seq: int, config_dir: str) -> None:
        self._jobs.put((
            lambda f: FocusDone(session, seq, f.focus(session, config_dir)),
            FocusDone(session, seq, UNAVAILABLE),
        ))

    def submit_verify(self, nonce: str, session: SessionRef) -> None:
        self._jobs.put((lambda f: VerifyDone(nonce, f.is_selected(session)), VerifyDone(nonce, False)))

    def submit_interrupt(self, session: SessionRef) -> None:
        self._jobs.put((lambda f: InterruptDone(session, f.send_escape(session)), InterruptDone(session, False)))

    def submit_title(self, session: SessionRef, config_dir: str) -> None:
        # Reading a transcript tail is slow over \\wsl.localhost, so it lives here
        # rather than on the Tk thread. A failure reports a None title.
        self._jobs.put((lambda f: TitleDone(session, f.title(session, config_dir)), TitleDone(session, None)))

    def forget(self, session: SessionRef) -> None:
        self._jobs.put((lambda f: f.forget(session), None))

    def stop(self, timeout: float = 5.0) -> None:
        self._jobs.put(_STOP)
        self._thread.join(timeout)

    def _run(self) -> None:
        com = _com_init()
        try:
            try:
                focus: Optional[WtFocus] = self._make_focus()
            except Exception:
                focus = None
            while True:
                job = self._jobs.get()
                if job is _STOP:
                    return
                self._run_job(job, focus)
        finally:
            _com_uninit(com)

    def _run_job(self, job: _Job, focus: Optional[WtFocus]) -> None:
        fn, safe = job
        try:
            if focus is None:
                raise RuntimeError("no focus backend")
            result = fn(focus)
        except Exception:
            result = safe
        if result is None:
            return
        try:
            self._on_result(result)
        except Exception:
            pass


def _com_init() -> Any:
    """Initialise COM on the calling thread. Returns the comtypes module, or None."""
    if sys.platform != "win32":
        return None
    try:
        import comtypes
    except ImportError:
        return None
    comtypes.CoInitialize()
    return comtypes


def _com_uninit(com: Any) -> None:
    if com is not None:
        try:
            com.CoUninitialize()
        except Exception:
            pass


def default_focus() -> WtFocus:
    """Build the WtFocus for this machine. Call it on the worker thread."""
    return WtFocus(make_uia_adapter())


def make_uia_adapter() -> Optional[UiaAdapter]:
    """The real UIA adapter, or None off Windows or without comtypes.

    Untested off a real Windows desktop. Must be created on a thread where COM
    is initialised (the FocusWorker thread).
    """
    if sys.platform != "win32":
        return None
    try:
        import comtypes.client  # noqa: F401
    except ImportError:
        return None
    try:
        return _ComUiaAdapter()
    except Exception:
        return None


class _ComUiaAdapter:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        from comtypes.client import CreateObject, GetModule

        GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia

        self._uia = uia
        self._auto = CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        self._user32 = user32 = ctypes.windll.user32
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND

    def list_tabs(self) -> List[Tab]:
        uia = self._uia
        auto = self._auto
        win_cond = auto.CreatePropertyCondition(uia.UIA_ClassNamePropertyId, WT_WINDOW_CLASS)
        tab_cond = auto.CreatePropertyCondition(
            uia.UIA_ControlTypePropertyId, uia.UIA_TabItemControlTypeId
        )
        windows = auto.GetRootElement().FindAll(uia.TreeScope_Children, win_cond)
        tabs: List[Tab] = []
        for i in range(windows.Length):
            window = windows.GetElement(i)
            handle = int(window.CurrentNativeWindowHandle or 0)
            items = window.FindAll(uia.TreeScope_Descendants, tab_cond)
            for j in range(items.Length):
                item = items.GetElement(j)
                try:
                    pattern = item.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(
                        uia.IUIAutomationSelectionItemPattern
                    )
                    selected = bool(pattern.CurrentIsSelected)
                except Exception:
                    continue
                tabs.append(Tab(handle, str(item.CurrentName or ""), selected, pattern))
        return tabs

    def select(self, tab: Tab) -> None:
        tab.element.Select()

    def bring_to_front(self, window_handle: int) -> bool:
        user32 = self._user32
        if user32.IsIconic(window_handle):
            user32.ShowWindow(window_handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow(window_handle)
        return int(user32.GetForegroundWindow() or 0) == window_handle

    def foreground_window(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def send_escape_key(self) -> None:
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class _Union(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _Union)]

        vk_escape, keyup, input_keyboard = 0x1B, 0x0002, 1
        events = (INPUT * 2)(
            INPUT(input_keyboard, _Union(ki=KEYBDINPUT(vk_escape, 0, 0, 0, 0))),
            INPUT(input_keyboard, _Union(ki=KEYBDINPUT(vk_escape, 0, keyup, 0, 0))),
        )
        self._user32.SendInput(2, events, ctypes.sizeof(INPUT))
