"""The About Tokitty window: content, single instance, raising, closing."""
from pathlib import Path

import pytest

from tokitty.qt import about as about_mod
from tokitty.qt import macos

pytestmark = pytest.mark.qt

COAT = ("gray", "tabby")


@pytest.fixture
def host(qapp, tmp_path):
    from tokitty import theme as T
    from tokitty.customize import Customization
    from tokitty.qt.host import QtHost
    from tokitty.qt.widget import TokittyQtWidget

    widget = TokittyQtWidget(T.Theme(), [Customization()], 1)
    widget.anim.stop()
    yield QtHost(widget, tmp_path)
    if about_mod._instance is not None:
        about_mod._instance.close()
    widget.close()


@pytest.fixture
def activations(monkeypatch):
    calls = []
    monkeypatch.setattr(macos, "activate_app", lambda *a, **kw: calls.append(1) or True)
    return calls


@pytest.fixture
def opened_urls(monkeypatch):
    urls = []
    monkeypatch.setattr(about_mod, "_open_url", urls.append)
    return urls


def labels(window):
    return [window.title.text(), window.version_label.text(), window.description.text(),
            window.copyright_label.text(), window.qt_label.text()]


def test_open_shows_the_version_description_and_links(host, activations, opened_urls, monkeypatch):
    monkeypatch.setattr("tokitty.app_version.version_text", lambda: "v1.2.3")
    window = about_mod.AboutWindow.open(host, lambda: COAT, frozen=True)
    assert window.isVisible()
    assert window.windowTitle() == "About Tokitty"
    assert window.title.text() == "Tokitty"
    assert window.version_label.text() == "v1.2.3"
    assert "cat-themed desktop widget" in window.description.text()
    assert window.copyright_label.text() == "Copyright © 2026 Nick Wolf. MIT License."
    assert window.source_label is None
    window.github_button.click()
    window.release_notes_button.click()
    assert opened_urls == [
        "https://github.com/nickwolf/tokitty",
        "https://github.com/nickwolf/tokitty/releases/tag/v1.2.3",
    ]


def test_opening_twice_keeps_one_instance_and_raises_it(host, activations):
    first = about_mod.AboutWindow.open(host, lambda: COAT)
    raised = []
    first.raise_ = lambda: raised.append(1)
    second = about_mod.AboutWindow.open(host, lambda: COAT)
    assert second is first
    assert raised == [1]
    assert len(activations) == 2


def test_it_is_registered_with_the_host_for_the_stay_on_top_hint(host, activations):
    host.widget.set_always_on_top(True)
    window = about_mod.AboutWindow.open(host, lambda: COAT)
    assert host.is_registered(window)
    assert window.windowFlags() & about_mod.Qt.WindowType.WindowStaysOnTopHint
    window.close()
    assert not host.is_registered(window)


def test_a_source_run_says_so(host, activations):
    window = about_mod.AboutWindow.open(host, lambda: COAT, frozen=False)
    assert window.source_label.text() == "Running from source"
    assert "licenses" not in window.links


def test_the_release_notes_link_needs_a_release_version(host, activations, monkeypatch):
    assert about_mod.release_notes_url("v0.6.0") == "https://github.com/nickwolf/tokitty/releases/tag/v0.6.0"
    assert about_mod.release_notes_url("dev") is None
    assert about_mod.release_notes_url("dryrun-abc123") is None
    monkeypatch.setattr("tokitty.app_version.version_text", lambda: "dev")
    window = about_mod.AboutWindow.open(host, lambda: COAT)
    assert "release_notes" not in window.links and "github" in window.links


def test_the_qt_notice_names_the_running_versions(host, activations):
    import PySide6
    from PySide6.QtCore import qVersion

    window = about_mod.AboutWindow.open(host, lambda: COAT)
    text = window.qt_label.text()
    assert f"Qt {qVersion()}" in text and f"PySide6 {PySide6.__version__}" in text
    assert "LGPL-3.0" in text and "see the licenses folder" in text


def test_licenses_dir_by_platform():
    assert about_mod.licenses_dir(False) is None
    win = about_mod.licenses_dir(True, "C:/Tokitty/Tokitty.exe", "win32")
    assert win == Path("C:/Tokitty/Tokitty.exe").parent / "licenses"
    mac = about_mod.licenses_dir(True, "/A/Tokitty.app/Contents/MacOS/Tokitty", "darwin")
    assert mac == Path("/A/Tokitty.app/Contents/Resources/licenses")


def test_the_licences_link_opens_the_folder_and_hides_when_it_is_missing(
        host, activations, opened_urls, monkeypatch, tmp_path):
    folder = tmp_path / "licenses"
    folder.mkdir()
    monkeypatch.setattr(about_mod, "licenses_dir", lambda frozen: folder)
    window = about_mod.AboutWindow.open(host, lambda: COAT, frozen=True)
    window.licenses_button.click()
    assert len(opened_urls) == 1 and opened_urls[0].startswith("file:") and "licenses" in opened_urls[0]
    window.close()

    folder.rmdir()
    window = about_mod.AboutWindow.open(host, lambda: COAT, frozen=True)
    assert "licenses" not in window.links and not hasattr(window, "licenses_button")


def test_the_cat_wears_the_given_coat(host, activations):
    from tokitty.qt.kit import sprite_image
    from tokitty.sprites import resolve_palette

    window = about_mod.AboutWindow.open(host, lambda: COAT)
    assert window.cat.image == sprite_image(resolve_palette(*COAT), "content", cell=about_mod.CAT_CELL)
    other = about_mod.AboutWindow.open(host, lambda: ("orange", "tabby"))
    assert other is window  # the open window is kept, not rebuilt


def test_escape_closes_without_quitting(host, activations, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    qapp.setQuitOnLastWindowClosed(False)
    quits = []
    qapp.aboutToQuit.connect(lambda: quits.append(1))
    window = about_mod.AboutWindow.open(host, lambda: COAT)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not window.isVisible() and about_mod._instance is None
    again = about_mod.AboutWindow.open(host, lambda: COAT)
    assert again is not window
    again.close_button.click()
    assert not again.isVisible() and about_mod._instance is None
    assert quits == []
    qapp.aboutToQuit.disconnect()
