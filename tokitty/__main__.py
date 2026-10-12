"""Entry point: python -m tokitty."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tokitty.accounts import load_accounts_result
from tokitty.activity import ActivityTracker
from tokitty.activity_watcher import ActivityWatcher
# Re-exported, not just imported: these were __main__ functions before they
# moved into app_core and are still imported from here by the tests.
from tokitty.app_controls import make_controls
from tokitty.app_core import (  # noqa: F401
    _STALE_HINTS,
    UI_REFRESH_MS,
    Discovery,
    _display_state_for,
    _next_last_good,
    _projection_text_for,
    build_units,
    initial_customization,
    initial_label,
    make_onboarder,
    make_usage_state,
    provider_tag_kind,
    publish_ntfy,
    publish_usage_notes,
    refresh_units,
    step_unit,
)
from tokitty.customize import (
    Customization,
    effective_palette,
    load_customization,
    save_customization,
)
from tokitty.distro_probe import RunningDistroProbe
from tokitty.lock import LockAcquisitionError, SingleInstanceLock, acquire_with_retry
from tokitty.paths import get_state_dir
from tokitty.poller import Poller  # noqa: F401  (patched through here by the tests)
from tokitty.providers import (
    DEFAULT_KIND,
    UnknownProviderError,
    get_provider,
)
# Re-exported, not just imported: these were __main__ functions before
# the provider seam and are still imported from here by the tests and
# by --debug-print below.
from tokitty.providers.claude import (  # noqa: F401
    build_fetch_fn,
    resolve_activity_sessions,
    resolve_projects_dir,
)
from tokitty.ntfy import Notifier
from tokitty.streamdock.window import InWindowViews, tk_view_factory
from tokitty.streamdock.wiring import DEFAULT_NAME, BIND_MARGIN_S, DeckStarter, apply_idle_cap, gather_inputs

# tkinter (and tokitty.ui, which imports it) is deliberately NOT imported
# at module level -- --debug-print must keep working on systems without a
# GUI toolkit installed (e.g. this project's own WSL dev environment).
# run_gui() imports both lazily, only when the GUI path actually runs.

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
# The ack waits for the window to be mapped, but not forever: a window manager
# that never reports a map must not strand the handover.
ACK_MAP_POLL_MS = 50
ACK_MAP_TIMEOUT = 5.0


def debug_print() -> int:
    from tokitty.accounts import load_accounts

    from tokitty import pricing

    accounts = load_accounts(get_state_dir())
    # table() prints any override warning to stderr on first load.
    print("prices: " + "; ".join(pricing.table().sources))
    for account in accounts or [None]:
        if account is not None:
            print(f"— {account.name} ({account.config_dir})")
        config_dir = account.config_dir if account else None
        try:
            provider = get_provider(account.provider if account else None)
        except UnknownProviderError as exc:
            print(f"provider: {exc}")
            continue
        if provider.kind != DEFAULT_KIND:
            print(f"provider: {provider.display_name}")

        result = provider.build_fetch_fn(config_dir)()
        print(f"status: {result.status}")
        if result.message:
            print(f"message: {result.message}")
        if result.source_description:
            print(f"credentials source: {result.source_description}")
        if result.snapshot is not None:
            s = result.snapshot
            print(f"session: {s.session_pct:.1f}% (resets {s.session_resets_at})")
            print(f"weekly:  {s.weekly_pct:.1f}% (resets {s.weekly_resets_at})")
            if s.credits_used is not None and s.credits_limit is not None:
                print(f"credits: ${s.credits_used:.2f} / ${s.credits_limit:.2f}")

        ledger = provider.resolve_ledger(config_dir)
        if ledger is not None:
            from tokitty.pricing import display_name
            from tokitty.settings import load_settings
            from tokitty.usage_display import format_money, format_tokens

            window = load_settings(get_state_dir()).usage_window
            scanner = ledger.make_scanner()
            status, failed_files, failed_rows = scanner.scan()
            usage = scanner.breakdown(window, status, failed_files, failed_rows)
            print(f"usage ({usage.window}, scan {usage.status}): {ledger.root}")
            for row in usage.models:
                cost = "--" if row.cost_usd is None else format_money(row.cost_usd)
                print(f"  {display_name(row.model):<22} {format_tokens(row.total_tokens):>8} {cost:>10}")
            prefix = ">= " if usage.is_partial_cost else ""
            print(f"  {'total':<22} {format_tokens(usage.total_tokens):>8} {prefix + format_money(usage.total_cost_usd):>10} at API rates")
        else:
            print("usage: no transcripts found")

        sessions_dir, distro_name = provider.resolve_activity_sessions(config_dir)
        if sessions_dir is not None:
            watcher = ActivityWatcher(sessions_dir, ActivityTracker(), distro_name=distro_name)
            watcher._tick_once()  # one-shot snapshot; no background thread for a single debug print
            activity = watcher.get_latest()
            if activity is not None:
                label = f" ({activity.tool_label})" if activity.tool_label else ""
                print(f"activity: {activity.state}{label}")

    return 0


def refresh_streamdock_plugin(state_dir) -> None:
    """Update an installed Stream Dock plugin to this build's copy. Never raises."""
    from tokitty.streamdock.install import _real_appdata, install_supported, refresh_streamdock

    if not install_supported():
        return
    try:
        if refresh_streamdock(state_dir=state_dir, appdata=_real_appdata()):
            print("tokitty: streamdock: plugin updated; restart VSD Craft to load it", file=sys.stderr)
    except Exception as exc:
        print(f"tokitty: streamdock: plugin refresh: {exc}", file=sys.stderr)



def run_gui(after_update_token: Optional[str] = None, apply_update: bool = False) -> int:
    import tkinter as tk

    from tokitty import update_controller
    from tokitty.ui import BG_COLOR, TokittyWindow

    state_dir = get_state_dir()
    lock = SingleInstanceLock(state_dir)
    try:
        # The old copy releases the lock as it hands over, so a new copy
        # started by an update waits for it instead of giving up.
        if after_update_token:
            acquire_with_retry(lock, 30)
        else:
            lock.acquire()
    except LockAcquisitionError:
        if not after_update_token:
            print("Tokitty is already running.", file=sys.stderr)
        return 1
    if after_update_token and update_controller.skips_ack(os.environ):
        lock.release()
        return 0

    from tokitty import first_run
    from tokitty.accounts import env_conflict_warning, load_accounts
    from tokitty.settings import load_settings, update_settings

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    debug_accounts = os.environ.get("TOKITTY_DEBUG_ACCOUNTS")

    # Under the lock and before anything else writes to the state dir, so two
    # near-simultaneous launches cannot both see a fresh dir. Debug launches
    # and the update hand-offs neither show the walkthrough nor mark it.
    show_walkthrough = first_run.classify(
        state_dir,
        load_settings(state_dir),
        excluded=bool(debug_state or debug_accounts == "2" or after_update_token or apply_update),
    )

    env_override_set = bool(os.environ.get("TOKITTY_CREDENTIALS"))
    home_relative_exists = (
        Path.home() / ".claude" / ".credentials.json"
    ).is_file()

    # One wsl.exe credential sweep for the whole launch, shared by the
    # walkthrough's discovery, run_discovery below and both per-account
    # resolvers in the unit loop. The sweep shells into every installed
    # distro, which starts a stopped one, so running it once per caller meant
    # waking every distro three times per launch, and since autostart (#20)
    # on every login.
    #
    # Disabled outright when credentials were already found natively, which
    # is the guard run_discovery has always had on its own sweep. The
    # resolvers never had it, so a launch with TOKITTY_CREDENTIALS set woke
    # every distro looking for something it had already been handed. The
    # walkthrough is the exception: it lists every install, so it needs the
    # sweeps whatever was found natively.
    from tokitty.wsl_probe import WslCredentialsCache

    wsl_credentials = WslCredentialsCache(
        enabled=show_walkthrough or not (env_override_set or home_relative_exists)
    )
    distro_probe = RunningDistroProbe()

    # Before tk.Tk(), and it has to stay before it: Windows refuses to
    # change a process's DPI awareness once the process owns a window, and
    # reports the refusal in the return value rather than by raising.
    from tokitty import dpi

    scale = dpi.init()

    root = tk.Tk()
    if sys.platform == "darwin" and not getattr(sys, "frozen", False):
        # A source run's Dock tile is otherwise the Python rocket. A frozen
        # build takes its icon from Tokitty.app instead.
        try:
            from tokitty import app_icon

            root.iconphoto(True, app_icon.photo(root))
        except Exception:
            pass
    if show_walkthrough:
        # The panes are built once from accounts.json, so the walkthrough
        # runs first, as a Toplevel of a withdrawn root. Nothing else starts
        # until it closes: no pollers, discovery thread or tray, which also
        # keeps the hook guard free for its installs.
        root.withdraw()
        from tokitty.first_run_ui import Walkthrough

        walkthrough = Walkthrough(
            root, state_dir, scale, credentials_cache=wsl_credentials,
            running_distros=distro_probe.get_running,
        )
        root.wait_window(walkthrough.toplevel)
        if env_override_set or home_relative_exists:
            # The sweep above was only for listing installs. With native
            # credentials the rest of the launch keeps the usual guard, or a
            # skipped walkthrough would read activity from a WSL install
            # while the limits come from this PC.
            wsl_credentials = WslCredentialsCache(enabled=False)

    accounts = load_accounts(state_dir)
    warning = env_conflict_warning(accounts)
    if warning:
        print(f"tokitty: {warning}", file=sys.stderr)

    pane_count = 2 if debug_accounts == "2" else (len(accounts) if accounts else 1)

    # Settings are read before the window exists so the stored opacity is
    # applied at construction. Loading them later made a cold start paint
    # fully opaque and then snap to the saved level. Re-read after the
    # walkthrough, which writes them.
    settings = load_settings(state_dir)

    usage_state = make_usage_state(settings)
    notifier = Notifier(state_dir)

    window = TokittyWindow(root, state_dir, pane_count=pane_count, opacity=settings.opacity, scale=scale)
    window.on_opacity_changed = lambda level: update_settings(state_dir, opacity=level)
    if show_walkthrough:
        # Built while withdrawn, so overrideredirect, topmost, geometry,
        # opacity and the colour-key window are all in place before anything
        # is visible.
        window.reveal()

    discovery = Discovery(
        state_dir, accounts_state=load_accounts_result(state_dir).state,
        env_override_set=env_override_set, home_relative_exists=home_relative_exists,
        wsl_credentials=wsl_credentials,
    )
    # The pending-hook-op retry belongs here, not in TokittyWindow.__init__:
    # it must run only after tk.Tk() has succeeded (a headless launch should
    # fail for lack of a display before ever probing WSL), and the gui-marked
    # tests that construct TokittyWindow directly (never through run_gui)
    # must keep seeing zero WSL calls.
    if not (debug_state or debug_accounts == "2"):
        discovery.start()

    if debug_state or debug_accounts == "2":
        fake = dict(
            state=debug_state or "content", session_pct=37.0, weekly_pct=62.0,
            session_reset_text="resets 9pm", weekly_reset_text="resets Fri",
            driving_tag="debug", credits_text=None, hint_text=None, dimmed=False,
        )
        for index, pane in enumerate(window.panes):
            resting = dict(fake, state="sleeping", dimmed=True,
                           hint_text="last seen 17:40")
            pane.render(**(fake if index == 0 else resting))
        root.mainloop()
        lock.release()
        return 0

    customization_store = load_customization(state_dir)

    from tokitty.migration import (
        CUSTOMIZATION_MIGRATION_KEY,
        LEGACY_ACCOUNT_LABELS_MIGRATION_KEY,
        mark_customization_migration_complete,
        migrate_default_customization,
        migrate_legacy_account_labels,
    )

    customization_store = migrate_default_customization(state_dir, accounts, customization_store)
    customization_store = migrate_legacy_account_labels(state_dir, accounts, customization_store)
    # The transformed data must be durable before either migration marker.
    # If the process stops after this save but before a marker, both
    # transforms are safe to retry on the next launch.
    save_customization(state_dir, customization_store)
    mark_customization_migration_complete(state_dir, CUSTOMIZATION_MIGRATION_KEY)
    mark_customization_migration_complete(state_dir, LEGACY_ACCOUNT_LABELS_MIGRATION_KEY)

    def apply_customization(pane, custom: Customization) -> None:
        pane.set_appearance(
            palette=effective_palette(custom),
            card_bg=custom.overrides.get("card_bg", BG_COLOR),
            bar_fill=custom.overrides.get("bar_fill", ""),
            colorway=custom.colorway, pattern=custom.pattern,
        )

    units = build_units(
        accounts=accounts, state_dir=state_dir, customization_store=customization_store,
        wsl_credentials=wsl_credentials, distro_probe=distro_probe, usage_state=usage_state,
    )
    for index, unit in enumerate(units):
        pane = window.panes[index]
        unit["pane"] = pane
        apply_customization(pane, unit["custom"])
        pane.set_appearance(label=unit["label"])

    def refresh_all():
        refresh_units(units)

    window.on_refresh_requested = refresh_all

    def warn(title: str, text: str) -> None:
        from tkinter import messagebox

        messagebox.showwarning(title, text, parent=root)

    controls = make_controls(
        state_dir, units, usage_state, customization_store, settings,
        apply_customization=apply_customization,
        # Tk does not consume the layout model, so nothing follows a save.
        on_layout_changed=lambda pane_index, custom: None,
        distro_probe=distro_probe,
        # The deck is built further down, once the panes exist.
        deck_fn=lambda: deck,
        label_fn=lambda i: window.panes[i]._label if i < len(window.panes) else None,
        warn=warn,
    )
    for name, value in controls.seams().items():
        setattr(window, name, value)
    autostart_backend = controls.autostart_backend
    from tokitty.tray import TrayManager
    from tokitty.accounts_ui import AccountsManager

    def open_accounts() -> None:
        matches = discovery.wsl_matches()
        AccountsManager.open(root, state_dir, discovered_matches=matches)

    window.on_open_accounts = open_accounts

    from tokitty.settings_ui import SettingsWindow

    window.open_settings = lambda pane_index: SettingsWindow.open(window, pane_index)
    if sys.platform == "darwin":
        window.register_app_menu_settings()
        window.register_about_panel(frozen=bool(getattr(sys, "frozen", False)))

    if settings.surprise_me:
        for index in range(len(units)):
            controls.on_customization_changed(index, "randomize", None)

    pane0 = window.panes[0]
    tray = TrayManager(root, lambda: window.build_menu_model(0), state_dir,
                       colorway=pane0._colorway, pattern=pane0._pattern,
                       on_action_done=window.notify_state_changed)

    window.on_quit = lambda: (tray.stop(), root.destroy())
    # The right-click menu rebuilds itself on every open, but pystray
    # builds its menu once, so any toggle made from the right-click menu
    # has to nudge the tray or the two disagree until restart.
    window.on_menu_action_done = tray.refresh
    if tray.available:
        tray_state = {"enabled": settings.tray_enabled}
        window.tray_enabled = lambda: tray_state["enabled"]

        def toggle_tray():
            tray_state["enabled"] = not tray_state["enabled"]
            tray.set_enabled(tray_state["enabled"])

        window.on_toggle_tray = toggle_tray

    if warning:
        window.panes[0].render(state="confused", session_pct=0.0, weekly_pct=0.0,
                               session_reset_text="—", weekly_reset_text="—", driving_tag="",
                               credits_text=None, hint_text=warning, dimmed=True)

    maybe_onboard = make_onboarder(state_dir, usage_state, tray.refresh)

    def pane_anchor(index: int):
        pane = units[index]["pane"].parent
        return pane.winfo_rootx() + 16, pane.winfo_rooty() + 16

    def account_name(index: int) -> str:
        account = units[index]["account"]
        return account.name if account else DEFAULT_NAME

    deck_views = InWindowViews(lambda: deck.runtime, account_name, pane_anchor, tk_view_factory(root))
    # A busy deck port is retried on the Tk thread for as long as an update's
    # handover can take, so a new copy launched by the updater still gets it.
    deck = DeckStarter(
        settings, units, distro_probe.get_running, palette_fn=lambda i: units[i]["pane"].palette,
        open_in_window=deck_views.open, after=root.after, cancel=root.after_cancel,
        deadline_s=update_controller.ACK_TIMEOUT + BIND_MARGIN_S,
    )
    deck.begin()
    refresh_streamdock_plugin(state_dir)

    from tokitty.streamdock.gui_state import StreamdockHolder
    from tokitty.streamdock.install import install_supported

    # The Settings tab updates this after an install or uninstall; the state
    # is read from it, not from the startup `settings`.
    streamdock_holder = StreamdockHolder(settings, state_dir, lambda: deck.runtime, deck=deck)
    window.streamdock_state = streamdock_holder.state
    window.streamdock_info = streamdock_holder.info
    window.streamdock_install = streamdock_holder.install
    window.streamdock_uninstall = streamdock_holder.uninstall
    window.streamdock_install_supported = install_supported

    def edit_streamdock_presets() -> None:
        from tokitty.streamdock.presets_ui import PresetsDialog

        def on_saved(presets) -> None:
            deck.set_presets(presets)

        PresetsDialog.open(
            root,
            state_dir,
            lambda: [u.get("account") for u in units],
            lambda i: window.panes[i]._label if i < len(window.panes) else None,
            on_saved,
        )

    window.on_edit_streamdock_presets = edit_streamdock_presets

    update_host = update_controller.TkHost(
        root,
        [window.root, window.content],
        tray,
        lock,
        state_dir,
        autostart_backend,
        quiet=apply_update,
        tray_enabled=lambda: load_settings(state_dir).tray_enabled,
    )
    updates = update_controller.UpdateController(state_dir, update_host)
    from tokitty.update_check import UpdateChecker, announcer
    from tokitty.update_dialog import UpdateUi

    # Plain-Python shadow state again: the tray menu reads it on pystray's thread.
    update_check_state = {"on": settings.update_check}
    checker = UpdateChecker(
        state_dir, updates.running, enabled=lambda: update_check_state["on"],
        on_available=announcer(state_dir, tray),
    )
    update_ui = UpdateUi(root, checker, updates)

    def toggle_update_check() -> None:
        update_check_state["on"] = not update_check_state["on"]
        update_settings(state_dir, update_check=update_check_state["on"])

    window.update_available_label = checker.menu_label
    window.on_install_update = update_ui.install
    window.on_check_updates = update_ui.check_now
    window.update_check_enabled = lambda: update_check_state["on"]
    window.on_toggle_update_check = toggle_update_check
    apply_exit = {"code": 1}

    notes_removed: set = set()

    def tick():
        updates.tick()
        checker.tick()
        # Show the discovery thread's hook warnings here, on the Tk thread, exactly
        # once -- see Discovery in app_core for why this can't be done from the
        # thread itself via root.after().
        hook_warnings = discovery.take_hook_warnings()
        if hook_warnings:
            # Deferred: showwarning is modal and would otherwise
            # block tick() from finishing and rescheduling itself
            # (root.after(UI_REFRESH_MS, tick), below) until the user
            # dismisses it. root.after(0, ...) runs it as its own
            # callback once this call returns, so tick's cadence is
            # never held up by it.
            def _show_hook_warnings(warnings=hook_warnings):
                from tkinter import messagebox

                messagebox.showwarning("Tokitty", "\n\n".join(warnings), parent=root)

            root.after(0, _show_hook_warnings)

        for unit in units:
            update = step_unit(
                unit, usage_state=usage_state, notifier=notifier,
                customization_store=customization_store, notes_removed=notes_removed,
                onboard=maybe_onboard, now=datetime.now(timezone.utc),
            )
            if update is not None:
                getattr(unit["pane"], "render" if update.kind == "limits" else "render_usage")(**update.kwargs)
        streamdock = deck.runtime
        if streamdock is not None:
            try:
                streamdock.tick(*gather_inputs(units))
                apply_idle_cap(units, streamdock.connected)
                deck_views.sync()
            except Exception as exc:
                print(f"tokitty: streamdock: tick: {exc}", file=sys.stderr)
        root.after(100 if updates.busy else UI_REFRESH_MS, tick)

    for unit in units:
        unit["poller"].start()
        unit["watcher"].start()
        unit["usage"].start()
    if tray.available and settings.tray_enabled:
        tray.start()
    root.after(UI_REFRESH_MS, tick)
    updates.startup(frozen=bool(getattr(sys, "frozen", False)))
    if after_update_token:
        # Inside the mainloop and once the window is mapped, so the first
        # window is up when the old copy hears from us.
        import time

        ack_deadline = time.monotonic() + ACK_MAP_TIMEOUT

        def send_ack() -> None:
            from tokitty.update_swap import write_ack

            if not window.root.winfo_ismapped() and time.monotonic() < ack_deadline:
                root.after(ACK_MAP_POLL_MS, send_ack)
                return
            try:
                write_ack(state_dir, after_update_token)
            except OSError as exc:
                print(f"tokitty: update: writing the ack: {exc}", file=sys.stderr)

        root.after(0, send_ack)
    if apply_update:
        def on_apply_done(result) -> None:
            apply_exit["code"] = {"installed": 0, "rolled_back": 2}.get(result.outcome, 1)
            if result.outcome != "installed":
                print(f"tokitty: update: {result.message}", file=sys.stderr)
                update_host.finish()

        updates.start_apply_newest(on_apply_done)

    try:
        root.mainloop()
    finally:
        tray.stop()
        deck_views.close_all()
        deck.stop()
        notifier.stop()
        for unit in units:
            unit["poller"].stop()
            unit["watcher"].stop()
            unit["usage"].stop()
        lock.release()

    return apply_exit["code"] if apply_update else 0


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-check" in argv:
        from tokitty.frozen import self_check

        return self_check()
    if "--check-for-update" in argv:
        from tokitty.updater import check_for_update_cli

        return check_for_update_cli()
    if "--debug-print" in argv:
        return debug_print()
    if "--install-hooks" in argv:
        from tokitty.hooks_install import install_hooks

        return install_hooks()
    if "--uninstall-hooks" in argv:
        from tokitty.hooks_install import uninstall_hooks

        return uninstall_hooks()
    if "--install-streamdock" in argv:
        from tokitty.streamdock.install import run_install

        return run_install()
    if "--uninstall-streamdock" in argv:
        from tokitty.streamdock.install import run_uninstall

        return run_uninstall()
    if "--install-autostart" in argv:
        from tokitty.autostart import install_autostart

        return install_autostart()
    if "--uninstall-autostart" in argv:
        from tokitty.autostart import uninstall_autostart

        return uninstall_autostart()
    token = None
    if "--after-update" in argv:
        from tokitty.update_swap import valid_token

        index = argv.index("--after-update") + 1
        token = argv[index] if index < len(argv) else None
        if not valid_token(token):
            print("tokitty: --after-update needs a token", file=sys.stderr)
            return 2
    if os.environ.get("TOKITTY_QT") == "1" and "--after-update" not in argv and "--apply-update" not in argv:
        # Source-only developer mode until #115; the update hand-offs keep
        # the Tk path (#113).
        from tokitty.qt.app import run_qt

        return run_qt()
    if token or "--apply-update" in argv:
        return run_gui(after_update_token=token, apply_update="--apply-update" in argv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
