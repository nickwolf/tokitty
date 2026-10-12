"""The macOS helper against a recording fake of the objc layer."""
from tokitty.qt import macos


class FakeObjC:
    def __init__(self, window=0x1000, responds=True, current=0x40, raises=()):
        self.window, self.responds, self.current, self.raises = window, responds, current, set(raises)
        self.calls = []

    def _log(self, kind, *args):
        self.calls.append((kind, *args))
        if kind in self.raises:
            raise RuntimeError(f"{kind} boom")

    def sel(self, name):
        return name

    def send_id(self, obj, sel):
        self._log("id", obj, sel)
        return self.window

    def send_bool_sel(self, obj, sel, arg):
        self._log("bool_sel", sel, arg)
        return self.responds

    def send_void_bool(self, obj, sel, value):
        self._log("void_bool", sel, value)

    def send_ulong(self, obj, sel):
        self._log("ulong", sel)
        return self.current

    def send_void_ulong(self, obj, sel, value):
        self._log("void_ulong", sel, value)


def run(fake, platform="darwin"):
    return macos.apply(7, fake, platform)


def test_both_steps_run_in_order():
    fake = FakeObjC()
    assert run(fake) == ["prevent_activation", "collection_behavior"]
    assert fake.calls == [
        ("id", 7, "window"),
        ("bool_sel", "respondsToSelector:", "_setPreventsActivation:"),
        ("void_bool", "_setPreventsActivation:", True),
        ("ulong", "collectionBehavior"),
        ("void_ulong", "setCollectionBehavior:", 0x40 | (1 << 0) | (1 << 8)),
    ]


def test_collection_behavior_ors_into_the_current_value():
    fake = FakeObjC(current=1 << 4)
    run(fake)
    assert fake.calls[-1][2] == (1 << 4) | 1 | (1 << 8)


def test_prevent_activation_is_skipped_when_the_selector_is_gone():
    fake = FakeObjC(responds=False)
    assert run(fake) == ["collection_behavior"]
    assert not [c for c in fake.calls if c[0] == "void_bool"]


def test_a_raising_step_logs_one_line_and_the_other_still_runs(capsys):
    fake = FakeObjC(raises={"void_bool"})
    assert run(fake) == ["collection_behavior"]
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "_setPreventsActivation:" in err[0]

    fake = FakeObjC(raises={"ulong"})
    assert run(fake) == ["prevent_activation"]
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_a_nil_window_skips_everything_with_one_line(capsys):
    fake = FakeObjC(window=0)
    assert run(fake) == []
    assert [c[0] for c in fake.calls] == ["id"]
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_unreachable_window_logs_once(capsys):
    assert run(FakeObjC(raises={"id"})) == []
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_nothing_happens_off_macos(capsys):
    for platform in ("win32", "linux"):
        fake = FakeObjC()
        assert run(fake, platform) == []
        assert fake.calls == []
    assert capsys.readouterr().err == ""


def test_import_has_no_toolkit():
    import subprocess
    import sys

    code = ("import sys\nsys.modules['PySide6'] = None\nsys.modules['tkinter'] = None\n"
            "import tokitty.qt.macos\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
