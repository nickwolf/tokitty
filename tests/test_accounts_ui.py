import json
import threading

import pytest

from tokitty.accounts import Account
from tokitty.accounts_ui import build_row_specs, reconcile_before_save
from tokitty.customize import Customization

_VALID_CREDENTIALS = json.dumps({"claudeAiOauth": {}})


def _is_expected_afterloop_race(args) -> bool:
    """True for the one specific, confirmed-benign exception this test
    file's background-thread tests can trigger: _run_mutation_off_thread's
    worker calls self.toplevel.after(...) after apply_account_mutation
    returns, and Tk's cross-thread after() hand-off only succeeds while
    the main thread is genuinely inside mainloop(). Tests never run a
    real mainloop() (needed to keep the test synchronous so it can make
    assertions) -- a real mainloop() was tried and empirically produces
    a much worse outcome, a hard "Fatal Python error: Aborted" crash
    from the interaction between Tcl's event loop and a concurrently
    running background thread doing real filesystem I/O, so that
    approach was reverted. In the actual app (run_gui), the main thread
    runs root.mainloop() continuously, so the equivalent call there has
    an active loop to dispatch into and this exception cannot occur --
    confirmed empirically by isolating the same after()-from-a-thread
    call with a real mainloop() running in a plain, non-test script."""
    return (
        args.exc_type is RuntimeError
        and "main thread is not in main loop" in str(args.exc_value)
    )


def _run_and_wait_for_mutation(action, root, monkeypatch, timeout=2.0):
    """Drive `action` (a call to _on_add/_on_remove), which spawns a
    daemon thread via _run_mutation_off_thread that calls
    apply_account_mutation and then on_done(result) ->
    toplevel.after(0, self._refresh_rows).

    Captures that Thread object (by subclassing threading.Thread for
    the duration of `action`) and joins it directly, rather than
    polling for a side effect or guessing a settle time -- this is a
    deterministic "has it fully finished" signal, including the
    trailing on_done()/after() call, not an approximation of one.

    Also installs a scoped threading.excepthook (restored automatically
    by monkeypatch at test teardown) that swallows only the one
    specific, confirmed-benign exception described in
    _is_expected_afterloop_race. Because the thread is actually joined
    here, before this function returns, that exception (if raised) is
    guaranteed to be dispatched while this test's monkeypatch is still
    in effect -- an earlier version of this helper only polled for a
    side effect with a fixed settle window, which left a real gap: a
    thread that happened to finish slightly late fired its exception
    under a *different, later* test's default (unfiltered) hook,
    producing PytestUnhandledThreadExceptionWarning attributed to
    unrelated tests elsewhere in the suite (observed empirically across
    repeated full-suite runs)."""
    original_hook = threading.excepthook

    def _filtering_hook(args):
        if _is_expected_afterloop_race(args):
            return
        original_hook(args)

    monkeypatch.setattr(threading, "excepthook", _filtering_hook)

    spawned = []
    real_thread_cls = threading.Thread

    class _RecordingThread(real_thread_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            spawned.append(self)

    monkeypatch.setattr(threading, "Thread", _RecordingThread)

    action()

    assert len(spawned) == 1, f"expected exactly one background thread, got {len(spawned)}"
    spawned[0].join(timeout=timeout)
    assert not spawned[0].is_alive(), "background mutation thread did not finish in time"

    # One more pump in case the after() call actually succeeded (it
    # won't, under this harness -- see _is_expected_afterloop_race --
    # but this costs nothing and keeps the helper correct if that ever
    # changes).
    try:
        root.update()
    except Exception:
        pass


def test_build_row_specs_remove_disabled_at_one_account():
    accounts = [Account(name="acct-v1-a", config_dir="/home/u/.claude")]
    rows = build_row_specs(accounts, {})
    assert len(rows) == 1
    assert rows[0].remove_enabled is False


def test_build_row_specs_remove_enabled_above_one_account():
    accounts = [
        Account(name="acct-v1-a", config_dir="/home/u/.claude-a"),
        Account(name="acct-v1-b", config_dir="/home/u/.claude-b"),
    ]
    rows = build_row_specs(accounts, {})
    assert all(row.remove_enabled for row in rows)


def test_build_row_specs_label_falls_back_without_showing_the_slug():
    accounts = [Account(name="acct-v1-deadbeef", config_dir="/home/u/.claude")]
    rows = build_row_specs(accounts, {})
    assert "acct-v1-deadbeef" not in rows[0].display_label


def test_build_row_specs_uses_stored_label_when_present():
    accounts = [Account(name="acct-v1-a", config_dir="/home/u/.claude")]
    store = {"acct-v1-a": Customization(colorway="black", pattern="tuxedo", label="Personal")}
    rows = build_row_specs(accounts, store)
    assert rows[0].display_label == "Personal"


def test_reconcile_before_save_reloads_from_disk(tmp_path):
    from tokitty.accounts import save_accounts

    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")])
    stale_in_memory = []  # a second dialog that never saw the first dialog's write
    reconciled = reconcile_before_save(tmp_path, stale_in_memory)
    assert [a.name for a in reconciled] == ["acct-v1-a"]


def test_reconcile_before_save_falls_back_to_in_memory_when_disk_empty(tmp_path):
    stale_in_memory = [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")]
    reconciled = reconcile_before_save(tmp_path, stale_in_memory)
    assert reconciled is stale_in_memory


@pytest.mark.gui
def test_accounts_manager_open_is_singleton_per_root(tmp_path):
    tk = pytest.importorskip("tkinter")
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts

    root = tk.Tk()
    try:
        save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")])
        first = AccountsManager.open(root, tmp_path)
        second = AccountsManager.open(root, tmp_path)
        assert first is second
        first._on_close()
        third = AccountsManager.open(root, tmp_path)
        assert third is not first
        third._on_close()
    finally:
        root.destroy()


@pytest.mark.gui
def test_on_add_absorbs_implicit_default_when_no_prior_accounts(tmp_path, monkeypatch):
    """First explicit Add, starting from implicit single-account mode
    (no accounts.json, customization.json only has SINGLE_KEY): the new
    slug must inherit the running "default" look, and random_look must
    never be called on this branch."""
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import load_accounts
    from tokitty.customize import SINGLE_KEY, Customization, save_customization, load_customization

    save_customization(tmp_path, {SINGLE_KEY: Customization(colorway="gray", pattern="solid")})

    config_dir = tmp_path / "new-claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_VALID_CREDENTIALS, encoding="utf-8")

    random_look_calls = []
    monkeypatch.setattr(
        accounts_ui, "random_look",
        lambda *a, **k: (random_look_calls.append(1), ("orange", "tabby"))[1],
    )

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: str(config_dir))
        _run_and_wait_for_mutation(mgr._on_add, root, monkeypatch)

        accounts = load_accounts(tmp_path)
        assert len(accounts) == 1
        slug = accounts[0].name

        store = load_customization(tmp_path)
        assert store[slug].colorway == "gray"
        assert store[slug].pattern == "solid"
        assert not random_look_calls, "random_look must not fire on the absorb-implicit-default path"
    finally:
        root.destroy()


@pytest.mark.gui
def test_on_add_second_account_rolls_random_look_not_absorb(tmp_path, monkeypatch):
    """Add with an already-active account present must roll a fresh
    random_look for the new slug, never absorb_implicit_default (there
    is no seeded "default" entry here, so if absorb ran instead, the
    new slug would end up with no customization entry at all)."""
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui, sprites
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts, load_accounts, Account
    from tokitty.customize import load_customization

    existing_dir = tmp_path / "existing-claude"
    existing_dir.mkdir()
    save_accounts(tmp_path, [Account(name="acct-v1-existing", config_dir=str(existing_dir))])

    new_dir = tmp_path / "new-claude"
    new_dir.mkdir()
    (new_dir / ".credentials.json").write_text(_VALID_CREDENTIALS, encoding="utf-8")

    absorb_calls = []
    real_absorb = accounts_ui.absorb_implicit_default

    def spy_absorb(*a, **k):
        absorb_calls.append(1)
        return real_absorb(*a, **k)

    monkeypatch.setattr(accounts_ui, "absorb_implicit_default", spy_absorb)

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: str(new_dir))
        _run_and_wait_for_mutation(mgr._on_add, root, monkeypatch)

        accounts = load_accounts(tmp_path)
        new_slug = [a.name for a in accounts if a.name != "acct-v1-existing"][0]

        store = load_customization(tmp_path)
        assert new_slug in store
        assert store[new_slug].colorway in sprites.COLORWAYS
        assert store[new_slug].pattern in sprites.PATTERNS
        assert not absorb_calls, "absorb_implicit_default must not fire when an account already existed"
    finally:
        root.destroy()


@pytest.mark.gui
def test_on_add_duplicate_reports_already_added_without_crashing(tmp_path, monkeypatch):
    """Exercises the real error path (validate_manual_path rejects a
    canonical duplicate -> messagebox.showerror) end-to-end and asserts
    no second account gets created. Also a regression guard for the
    `messagebox` name disappearing from accounts_ui's module namespace
    (e.g. an import accidentally dropped in a future edit): if
    `accounts_ui.messagebox` didn't exist, the monkeypatch.setattr below
    would fail with AttributeError before mgr._on_add() even runs.

    Note: this does NOT reproduce the original tk.messagebox
    AttributeError bug the fix addressed -- that specific crash turned
    out not to occur even in the pre-fix code, because this module also
    imports `simpledialog`, and CPython's tkinter/simpledialog.py itself
    does `from tkinter import messagebox` at module scope, which as a
    side effect binds `messagebox` onto the `tkinter` package object
    for the rest of the process. So `tk.messagebox` was already
    incidentally reachable by the time _on_add() ran, regardless of
    whether accounts_ui.py imported it directly (verified empirically
    by reverting to the brief's exact original call site+imports and
    calling _on_add() end-to-end: it did not raise). The explicit
    `from tkinter import messagebox` import is still correct practice
    -- it doesn't rely on that undocumented, version-fragile stdlib
    implementation detail -- but the crash it was believed to prevent
    was never actually reachable in this file as given."""
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts, load_accounts, Account

    existing_dir = tmp_path / "existing-claude"
    existing_dir.mkdir()
    (existing_dir / ".credentials.json").write_text(_VALID_CREDENTIALS, encoding="utf-8")
    save_accounts(tmp_path, [Account(name="acct-v1-existing", config_dir=str(existing_dir))])

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: str(existing_dir))
        errors = []
        # Never let the real modal messagebox pop up -- under a live
        # display it would block on wait_window with no one to click it.
        monkeypatch.setattr(accounts_ui.messagebox, "showerror", lambda *a, **k: errors.append(a))

        mgr._on_add()  # must not raise

        assert len(errors) == 1
        assert "already added" in errors[0][1]
        assert [a.name for a in load_accounts(tmp_path)] == ["acct-v1-existing"]
    finally:
        root.destroy()


@pytest.mark.gui
def test_on_remove_deletes_from_accounts_but_keeps_customization_orphaned(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts, load_accounts, Account
    from tokitty.customize import Customization, save_customization, load_customization

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    save_accounts(tmp_path, [
        Account(name="acct-v1-a", config_dir=str(dir_a)),
        Account(name="acct-v1-b", config_dir=str(dir_b)),
    ])
    save_customization(tmp_path, {
        "acct-v1-a": Customization(colorway="black", pattern="tuxedo"),
        "acct-v1-b": Customization(colorway="gray", pattern="solid", label="Bee"),
    })

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        _run_and_wait_for_mutation(lambda: mgr._on_remove("acct-v1-b", str(dir_b)), root, monkeypatch)

        assert [a.name for a in (load_accounts(tmp_path) or [])] == ["acct-v1-a"]
        store = load_customization(tmp_path)
        assert store["acct-v1-b"].label == "Bee"
        assert store["acct-v1-b"].colorway == "gray"
    finally:
        root.destroy()


@pytest.mark.gui
def test_on_rename_updates_label_by_slug_only(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts, load_accounts, Account
    from tokitty.customize import Customization, save_customization, load_customization

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir=str(dir_a))])
    save_customization(tmp_path, {"acct-v1-a": Customization(colorway="black", pattern="tuxedo")})

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: "My Personal Cat")
        mgr._on_rename("acct-v1-a")

        store = load_customization(tmp_path)
        assert store["acct-v1-a"].label == "My Personal Cat"
        assert store["acct-v1-a"].colorway == "black"

        accounts = load_accounts(tmp_path)
        assert accounts[0].name == "acct-v1-a"
        assert accounts[0].config_dir == str(dir_a)
    finally:
        root.destroy()
