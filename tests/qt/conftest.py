import importlib.util
import os

import pytest

# Qt tests run offscreen unless the caller picked a platform. Set before any
# QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Without PySide6 these modules cannot even be imported, so leave them out of
# collection (a conftest-level importorskip would be a collection error).
collect_ignore_glob = [] if importlib.util.find_spec("PySide6") else ["*.py"]


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_world(qapp, tmp_path, monkeypatch):
    """A real widget, QtHost and Controls over fake units, with every Settings
    window closed afterwards. Returns a factory; call it once per test."""
    from types import SimpleNamespace

    from tokitty import app_controls, autostart
    from tokitty import settings as settings_mod
    from tokitty import theme as T
    from tokitty.app_core import initial_label, make_usage_state
    from tokitty.customize import Customization
    from tokitty.qt import settings as qt_settings
    from tokitty.qt.app import apply_customization
    from tokitty.qt.host import QtHost
    from tokitty.qt.widget import TokittyQtWidget

    opened = []
    monkeypatch.setattr(autostart, "get_backend", lambda: None)

    class FakeUsage:
        def rebuild_for_window(self):
            pass

    def build(count=2, theme=None, customs=None, warn=None, tag_kinds=None):
        state_dir = tmp_path / f"state{len(opened)}"
        state_dir.mkdir()
        customs = customs or [Customization() for _ in range(count)]
        tag_kinds = tag_kinds or [None] * len(customs)
        widget = TokittyQtWidget(theme or T.Theme(cat=T.CatStyle(wander=False)), customs, len(customs))
        widget.anim.stop()
        units = [{"key": f"k{i}", "account": None, "tag_kind": kind, "pane": pane,
                  "usage": FakeUsage(), "custom": c}
                 for i, (pane, c, kind) in enumerate(zip(widget.panes, customs, tag_kinds))]
        for u in units:
            if u["tag_kind"]:
                u["pane"].set_appearance(label=initial_label(None, u["custom"], u["tag_kind"]))
        settings = settings_mod.load_settings(state_dir)
        usage_state = make_usage_state(settings)
        store = {u["key"]: c for u, c in zip(units, customs)}
        host = QtHost(widget, state_dir)
        warnings = []
        controls = app_controls.make_controls(
            state_dir, units, usage_state, store, settings,
            apply_customization=apply_customization,
            on_layout_changed=host.on_layout_changed,
            distro_probe=SimpleNamespace(get_running=lambda: []),
            deck_fn=lambda: None,
            label_fn=lambda i: widget.panes[i]._label,
            warn=warn or (lambda title, text: warnings.append((title, text))),
        )
        host.attach(controls)
        widget.on_menu_action_done = host.notify_state_changed
        world = SimpleNamespace(widget=widget, host=host, controls=controls, units=units,
                                state_dir=state_dir, usage_state=usage_state, store=store,
                                warnings=warnings)
        opened.append(world)
        return world

    yield build
    window = qt_settings._instance
    if window is not None:
        window.close()
    for world in opened:
        world.widget.hide()
