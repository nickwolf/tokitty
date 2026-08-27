import json
import threading
import time

import pytest

from tokitty.accounts import Account
from tokitty.accounts_ui import (
    build_discovered_path_specs,
    build_row_specs,
    reconcile_before_save,
)
from tokitty.customize import Customization

_VALID_CREDENTIALS = json.dumps({"claudeAiOauth": {}})


def _pump_until(root, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Tk-thread-polled background work did not finish in time"


def _run_and_wait_for_mutation(
    action, root, monkeypatch, timeout=2.0, manager=None
):
    """Drive `action` (a call to _on_add/_on_remove), which spawns a
    daemon thread via _run_mutation_off_thread that calls
    apply_account_mutation and publishes its result to a lock-protected
    state object. The Tk thread polls that state and finishes the mutation.

    Captures that Thread object (by subclassing threading.Thread for
    the duration of `action`) and joins it directly. If a manager is
    supplied, first let its independently spawned startup retry finish,
    matching the disabled-button behavior of the real dialog and keeping
    the two hook operations serialized."""
    if manager is not None:
        _pump_until(root, lambda: not manager._retry_in_flight, timeout)

    spawned = []
    real_thread_cls = threading.Thread

    class _RecordingThread(real_thread_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.submitted_target = k.get("target", a[1] if len(a) > 1 else None)
            spawned.append(self)

    monkeypatch.setattr(threading, "Thread", _RecordingThread)

    action()

    assert len(spawned) == 1, f"expected exactly one background thread, got {len(spawned)}"
    spawned[0].join(timeout=timeout)
    assert not spawned[0].is_alive(), "background mutation thread did not finish in time"

    if manager is not None:
        _pump_until(root, lambda: not manager._mutation_in_flight, timeout)
    return spawned[0]


def test_mutation_completion_is_polled_only_from_the_tk_thread(tmp_path, monkeypatch):
    """A filesystem worker must never call any Tk API, including after()."""
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager

    main_thread_id = threading.get_ident()

    class FakeToplevel:
        def __init__(self):
            self.after_calls = []

        def winfo_children(self):
            return []

        def winfo_exists(self):
            return True

        def after(self, delay, callback):
            self.after_calls.append((threading.get_ident(), callback))

    class Success:
        ok = True
        message = "installed"

    monkeypatch.setattr(
        accounts_ui,
        "apply_account_mutation",
        lambda *args: Success(),
    )

    spawned = []
    real_thread_cls = threading.Thread

    class RecordingThread(real_thread_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self)

    monkeypatch.setattr(threading, "Thread", RecordingThread)

    manager = AccountsManager.__new__(AccountsManager)
    manager.state_dir = tmp_path
    manager.toplevel = FakeToplevel()
    manager._mutation_in_flight = False
    manager._retry_in_flight = False
    manager._pending_hook_failure = False
    manager._refresh_rows = lambda: None

    manager._start_mutation([], "install", str(tmp_path))
    assert len(spawned) == 1
    spawned[0].join(timeout=2.0)
    assert not spawned[0].is_alive()

    assert [thread_id for thread_id, _ in manager.toplevel.after_calls] == [main_thread_id]
    manager.toplevel.after_calls[0][1]()
    assert manager._mutation_in_flight is False


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


def test_build_discovered_path_specs_converts_credentials_to_config_and_sessions_dirs():
    specs = build_discovered_path_specs([
        ("Ubuntu", "/home/nick/.claude-work/.credentials.json")
    ])

    assert specs[0].config_dir == r"\\wsl.localhost\Ubuntu\home\nick\.claude-work"
    assert specs[0].sessions_dir == (
        r"\\wsl.localhost\Ubuntu\home\nick\.claude-work\tokitty\sessions"
    )


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


def test_reconcile_before_save_respects_present_valid_empty_file(tmp_path):
    from tokitty.accounts import save_accounts

    save_accounts(tmp_path, [])
    stale_in_memory = [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")]
    assert reconcile_before_save(tmp_path, stale_in_memory) == []


@pytest.mark.gui
def test_accounts_manager_open_is_singleton_per_root(tmp_path):
    tk = pytest.importorskip("tkinter")
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts

    root = tk.Tk()
    try:
        save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")])
        first = AccountsManager.open(root, tmp_path)
        second = AccountsManager.open(
            root,
            tmp_path,
            discovered_matches=[("Ubuntu", "/home/u/.claude-b/.credentials.json")],
        )
        assert first is second
        assert first._discovered_paths[0].config_dir.endswith(r"\home\u\.claude-b")
        first._on_close()
        third = AccountsManager.open(root, tmp_path)
        assert third is not first
        third._on_close()
    finally:
        root.destroy()


@pytest.mark.gui
def test_discovered_match_renders_clickable_add_row(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty.accounts_ui import AccountsManager

    root = tk.Tk()
    try:
        mgr = AccountsManager(
            root,
            tmp_path,
            discovered_matches=[
                ("Ubuntu", "/home/nick/.claude-work/.credentials.json")
            ],
        )
        selected = []
        monkeypatch.setattr(mgr, "_on_add_discovered", lambda path: selected.append(path))
        discovered_buttons = [
            child
            for frame in mgr.toplevel.winfo_children()
            for child in frame.winfo_children()
            if isinstance(child, tk.Button) and child.cget("text") == "Add"
        ]

        assert len(discovered_buttons) == 1
        mgr._retry_in_flight = False
        mgr._update_mutation_controls()
        discovered_buttons[0].invoke()
        assert selected == [r"\\wsl.localhost\Ubuntu\home\nick\.claude-work"]
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
        _run_and_wait_for_mutation(mgr._on_add, root, monkeypatch, manager=mgr)

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
    from tokitty.customize import Customization, load_customization, save_customization

    existing_dir = tmp_path / "existing-claude"
    existing_dir.mkdir()
    save_accounts(tmp_path, [Account(name="acct-v1-existing", config_dir=str(existing_dir))])
    save_customization(tmp_path, {
        "unrelated": Customization(colorway="gray", pattern="solid", label="Keep me")
    })

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
        _run_and_wait_for_mutation(mgr._on_add, root, monkeypatch, manager=mgr)

        accounts = load_accounts(tmp_path)
        new_slug = [a.name for a in accounts if a.name != "acct-v1-existing"][0]

        store = load_customization(tmp_path)
        assert new_slug in store
        assert store["unrelated"].label == "Keep me"
        assert store[new_slug].colorway in sprites.COLORWAYS
        assert store[new_slug].pattern in sprites.PATTERNS
        assert not absorb_calls, "absorb_implicit_default must not fire when an account already existed"
    finally:
        root.destroy()


@pytest.mark.gui
def test_on_add_to_valid_empty_file_is_not_implicit_default_mode(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import load_accounts_result, save_accounts
    from tokitty.customize import SINGLE_KEY, Customization, load_customization, save_customization

    save_accounts(tmp_path, [])
    save_customization(tmp_path, {
        SINGLE_KEY: Customization(colorway="gray", pattern="solid")
    })
    config_dir = tmp_path / "new-claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_VALID_CREDENTIALS, encoding="utf-8")
    monkeypatch.setattr(accounts_ui, "random_look", lambda *a, **k: ("black", "tuxedo"))

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: str(config_dir))
        _run_and_wait_for_mutation(mgr._on_add, root, monkeypatch, manager=mgr)

        result = load_accounts_result(tmp_path)
        assert result.state == "valid_non_empty"
        custom = load_customization(tmp_path)[result.accounts[0].name]
        assert (custom.colorway, custom.pattern) == ("black", "tuxedo")
    finally:
        root.destroy()


@pytest.mark.gui
def test_malformed_accounts_file_blocks_add_without_overwriting(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager

    path = tmp_path / "accounts.json"
    path.write_text("{not json", encoding="utf-8")
    errors = []
    prompted = []
    monkeypatch.setattr(accounts_ui.messagebox, "showerror", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: prompted.append(1))

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        _pump_until(root, lambda: not mgr._retry_in_flight)
        mgr._on_add()
        mgr._on_remove("acct-v1-a", "/home/u/.claude")

        assert len(errors) == 2
        assert all("malformed" in error[1] for error in errors)
        assert prompted == []
        assert path.read_text(encoding="utf-8") == "{not json"
    finally:
        root.destroy()


@pytest.mark.gui
def test_mutation_guard_blocks_second_add_or_remove_until_completion(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import Account, save_accounts

    accounts = [
        Account(name="acct-a", config_dir="/home/u/.claude-a"),
        Account(name="acct-b", config_dir="/home/u/.claude-b"),
    ]
    save_accounts(tmp_path, accounts)
    started = []
    monkeypatch.setattr(
        accounts_ui,
        "_run_mutation_off_thread",
        lambda *args: started.append(args),
    )
    prompts = []
    monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: prompts.append(1))
    errors = []
    monkeypatch.setattr(accounts_ui.messagebox, "showerror", lambda *a, **k: errors.append(a))

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)

        mgr._on_remove("acct-b", "/home/u/.claude-b")
        assert started == [], "mutations must wait for the startup hook retry"

        _pump_until(root, lambda: not mgr._retry_in_flight)
        mgr._on_remove("acct-b", "/home/u/.claude-b")

        assert mgr._mutation_in_flight is True
        assert len(started) == 1
        controls = []
        for frame in mgr.toplevel.winfo_children():
            controls.extend(
                child for child in frame.winfo_children()
                if getattr(child, "_account_mutation_control", False)
            )
        controls.extend(
            child for child in mgr.toplevel.winfo_children()
            if getattr(child, "_account_mutation_control", False)
        )
        assert controls and all(control.cget("state") == "disabled" for control in controls)

        mgr._on_add()
        mgr._on_remove("acct-a", "/home/u/.claude-a")
        assert len(started) == 1
        assert prompts == []

        class Success:
            ok = True
            message = "installed"

        mgr._finish_mutation(Success())
        assert mgr._mutation_in_flight is False

        class Failure:
            ok = False
            message = "hook install failed"

        mgr._finish_mutation(Failure())
        assert mgr._mutation_in_flight is False
        assert mgr._pending_hook_failure is True
        mgr._on_add()
        assert prompts == []
        assert "hook install failed" in errors[-1][1]
    finally:
        root.destroy()


@pytest.mark.gui
def test_mutation_guard_survives_close_and_reopen(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import Account, save_accounts

    accounts = [
        Account(name="acct-a", config_dir="/home/u/.claude-a"),
        Account(name="acct-b", config_dir="/home/u/.claude-b"),
    ]
    save_accounts(tmp_path, accounts)

    mutation_started = threading.Event()
    release_mutation = threading.Event()
    mutation_calls = []
    retry_calls = []

    class Success:
        ok = True
        message = "installed"

    def blocking_mutation(*args):
        mutation_calls.append(args)
        mutation_started.set()
        assert release_mutation.wait(timeout=5.0)
        return Success()

    monkeypatch.setattr(accounts_ui, "apply_account_mutation", blocking_mutation)
    monkeypatch.setattr(
        accounts_ui,
        "retry_pending_hook_op",
        lambda state_dir: retry_calls.append(state_dir),
    )
    prompts = []
    monkeypatch.setattr(
        accounts_ui.simpledialog,
        "askstring",
        lambda *args, **kwargs: prompts.append(1),
    )

    root = tk.Tk()
    second = None
    try:
        first = AccountsManager.open(root, tmp_path)
        _pump_until(root, lambda: not first._retry_in_flight)
        first._on_remove("acct-b", "/home/u/.claude-b")
        assert mutation_started.wait(timeout=2.0)

        first._on_close()
        second = AccountsManager.open(root, tmp_path)

        assert second is not first
        assert second._mutation_in_flight is True
        assert second._retry_in_flight is False
        assert retry_calls == [tmp_path]

        second._on_add()
        second._on_remove("acct-a", "/home/u/.claude-a")
        assert prompts == []
        assert len(mutation_calls) == 1

        add_button = next(
            child
            for child in second.toplevel.winfo_children()
            if isinstance(child, tk.Button) and child.cget("text") == "Add by path…"
        )
        assert add_button.cget("state") == "disabled"

        release_mutation.set()
        _pump_until(root, lambda: not second._mutation_in_flight)
        assert add_button.cget("state") == "normal"
        assert len(mutation_calls) == 1
    finally:
        release_mutation.set()
        if second is not None and second.toplevel.winfo_exists():
            second._on_close()
        root.destroy()


@pytest.mark.gui
def test_readd_uses_backfilled_slug_and_preserves_orphaned_customization(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import Account, load_accounts, save_accounts, save_identity_history
    from tokitty.customize import Customization, load_customization, save_customization

    existing_dir = tmp_path / "existing"
    existing_dir.mkdir()
    readd_dir = tmp_path / "work"
    readd_dir.mkdir()
    (readd_dir / ".credentials.json").write_text(_VALID_CREDENTIALS, encoding="utf-8")
    save_accounts(tmp_path, [Account(name="Personal", config_dir=str(existing_dir))])
    from tokitty.accounts import canonicalize_locator
    save_identity_history(tmp_path, {canonicalize_locator(str(readd_dir)): "Work"})
    original = Customization(colorway="gray", pattern="solid", label="Work cat")
    save_customization(tmp_path, {"Work": original})
    random_calls = []
    monkeypatch.setattr(accounts_ui, "random_look", lambda *a, **k: random_calls.append(1))

    root = tk.Tk()
    try:
        mgr = AccountsManager(root, tmp_path)
        monkeypatch.setattr(accounts_ui.simpledialog, "askstring", lambda *a, **k: str(readd_dir))
        _run_and_wait_for_mutation(mgr._on_add, root, monkeypatch, manager=mgr)

        assert [account.name for account in load_accounts(tmp_path)] == ["Personal", "Work"]
        assert load_customization(tmp_path)["Work"] == original
        assert random_calls == []
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
        _pump_until(root, lambda: not mgr._retry_in_flight)
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
    from tokitty.accounts import (
        Account,
        canonicalize_locator,
        load_accounts,
        load_identity_history,
        save_accounts,
    )
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
        _run_and_wait_for_mutation(
            lambda: mgr._on_remove("acct-v1-b", str(dir_b)),
            root,
            monkeypatch,
            manager=mgr,
        )

        assert [a.name for a in (load_accounts(tmp_path) or [])] == ["acct-v1-a"]
        assert load_identity_history(tmp_path)[canonicalize_locator(str(dir_b))] == "acct-v1-b"
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


@pytest.mark.gui
def test_init_retries_pending_hook_op_off_the_tk_thread(tmp_path, monkeypatch):
    """Controller ruling (Finding 3, second review pass): retry_pending_hook_op
    (hooks_install.py, Task 8/12) must run off the Tk thread from __init__,
    not synchronously -- per the design spec, a stuck wsl.exe call or a slow
    filesystem there must not freeze the UI, the same reason
    apply_account_mutation already runs off-thread for add/remove.

    Rows must build synchronously in __init__ (via _build() ->
    _refresh_rows()) using whatever pre-retry state is already on disk,
    strictly before the retry is even dispatched -- this ordering is not a
    race: _build() is a plain statement that fully completes, in the Tk
    thread, before the next statement starts the retry's background
    thread, so refresh_rows can only ever be the first entry recorded.
    retry_pending_hook_op itself is confirmed to actually run, off-thread,
    by joining that thread -- same join-based technique the add/remove
    tests above use via _run_and_wait_for_mutation."""
    tk = pytest.importorskip("tkinter")
    from tokitty import accounts_ui
    from tokitty.accounts_ui import AccountsManager

    call_order = []
    monkeypatch.setattr(
        accounts_ui, "retry_pending_hook_op",
        lambda state_dir: call_order.append(("retry", state_dir)),
    )
    real_refresh_rows = AccountsManager._refresh_rows

    def spy_refresh_rows(self):
        call_order.append(("refresh_rows", None))
        return real_refresh_rows(self)

    monkeypatch.setattr(AccountsManager, "_refresh_rows", spy_refresh_rows)

    root = tk.Tk()
    holder = {}
    try:
        retry_thread = _run_and_wait_for_mutation(
            lambda: holder.__setitem__("mgr", AccountsManager(root, tmp_path)),
            root, monkeypatch,
        )
        assert getattr(retry_thread.submitted_target, "__self__", None) is not holder["mgr"], (
            "the retry worker must not retain the Tk-owning AccountsManager"
        )
        assert call_order[0] == ("refresh_rows", None), (
            "rows must build synchronously in __init__, before the retry is dispatched"
        )
        assert ("retry", tmp_path) in call_order, (
            "retry_pending_hook_op must fire, off-thread, with the manager's state_dir"
        )
    finally:
        holder["mgr"]._on_close()
        root.destroy()


@pytest.mark.gui
def test_destroying_root_cancels_pending_manager_callbacks(tmp_path):
    """A manager may still be polling its retry worker when the app exits.

    Tk does not cancel ``after`` timers when ``destroy`` removes the Python
    command they target.  Leaving the timer behind makes a later Tcl event
    pass invoke an invalid command, which is especially visible in the GUI
    suite's repeated create/destroy-root lifecycle.
    """
    tk = pytest.importorskip("tkinter")
    from tokitty.accounts_ui import AccountsManager

    root = tk.Tk()
    AccountsManager(root, tmp_path)
    pending_before = set(root.tk.splitlist(root.tk.call("after", "info")))
    assert pending_before, "manager construction must schedule its retry poll"

    root.destroy()

    pending_after = set(root.tk.splitlist(root.tk.call("after", "info")))
    assert pending_before.isdisjoint(pending_after)
