from unittest.mock import Mock

from tokitty.settings import load_settings
from tokitty.tray import TrayManager


class FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, delay, fn):
        self.after_calls.append((delay, fn))


def _managers(tmp_path, **overrides):
    root = FakeRoot()
    icons = []

    def icon_factory(image, menu_model, wrap, title):
        icon = Mock()
        icon._args = (image, menu_model, wrap, title)
        icons.append(icon)
        return icon

    kwargs = dict(
        root=root,
        menu_provider=lambda: ["model"],
        state_dir=tmp_path,
        coat="orange_tabby",
        icon_factory=icon_factory,
        image_factory=lambda coat: f"image:{coat}",
    )
    kwargs.update(overrides)
    return TrayManager(**kwargs), root, icons


def test_available_with_working_factories(tmp_path):
    mgr, _, _ = _managers(tmp_path)
    assert mgr.available is True


def test_start_builds_icon_and_thread(tmp_path):
    mgr, _, icons = _managers(tmp_path)
    mgr.start()
    assert mgr._icon is not None
    assert mgr._thread is not None
    # icon_factory was called with the provider's model on the start path.
    assert icons[-1]._args[1] == ["model"]


def test_action_wrapping_marshals_to_main_thread(tmp_path):
    mgr, root, _ = _managers(tmp_path)
    sentinel = lambda: None  # noqa: E731
    mgr._wrap(sentinel)("icon", "item")   # pystray calls with (icon, item)
    assert root.after_calls == [(0, sentinel)]


def test_stop_calls_icon_stop_and_is_noop_before_start(tmp_path):
    mgr, _, _ = _managers(tmp_path)
    mgr.stop()  # before start: no error
    mgr.start()
    icon = mgr._icon
    mgr.stop()
    icon.stop.assert_called_once()
    assert mgr._icon is None


def test_set_enabled_persists_and_toggles(tmp_path):
    mgr, _, icons = _managers(tmp_path)
    mgr.set_enabled(False)
    assert load_settings(tmp_path).tray_enabled is False
    mgr.set_enabled(True)
    assert load_settings(tmp_path).tray_enabled is True
    assert mgr._icon is not None  # re-enabling started a fresh icon


def test_guard_image_factory_raises(tmp_path):
    def boom(coat):
        raise RuntimeError("no PIL")
    mgr, _, icons = _managers(tmp_path, image_factory=boom)
    assert mgr.available is False
    mgr.start()
    assert icons == []  # start is a no-op


def test_guard_icon_factory_raises(tmp_path):
    def boom(image, menu_model, wrap, title):
        raise RuntimeError("no pystray backend")
    mgr, _, _ = _managers(tmp_path, icon_factory=boom)
    assert mgr.available is False
    mgr.start()  # no crash
