"""The Qt entry point (TOKITTY_QT=1): run_qt, the counterpart of __main__.run_gui.

The unit construction, the tick step, onboarding and discovery all come from
app_core, so what a pane shows is decided in one place. Everything the Tk path
has beyond that (tray, Stream Dock, update check, Settings, accounts manager,
walkthrough) is absent here until its own issue (#112 to #114). PySide6 is
imported inside run_qt, so importing this module never needs it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from tokitty.app_core import (
    UI_REFRESH_MS,
    Discovery,
    build_units,
    make_onboarder,
    make_usage_state,
    refresh_units,
    step_unit,
)

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
POSITION_FILENAME = "position.json"  # ui.POSITION_FILENAME, which cannot be imported without Tk
# ui.VIEW_MODE_ITEMS, for the same reason.
VIEW_MODE_ITEMS = [("limits", "Limits"), ("models", "Per-model")]
MISSING_PYSIDE6 = (
    'tokitty: TOKITTY_QT is set but PySide6 is not installed (pip install -e ".[qt]")'
)


def run_qt() -> int:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(MISSING_PYSIDE6, file=sys.stderr)
        return 1

    from tokitty.lock import LockAcquisitionError, SingleInstanceLock
    from tokitty.paths import get_state_dir

    state_dir = get_state_dir()
    lock = SingleInstanceLock(state_dir)
    try:
        lock.acquire()
    except LockAcquisitionError:
        print("Tokitty is already running.", file=sys.stderr)
        return 1
    try:
        return _run_locked(state_dir)
    finally:
        lock.release()


def _run_locked(state_dir) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from tokitty import first_run, theme
    from tokitty.accounts import env_conflict_warning, load_accounts, load_accounts_result
    from tokitty.customize import Customization, load_customization, save_customization
    from tokitty.settings import load_settings, update_settings
    from tokitty.qt.widget import TokittyQtWidget

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    debug_accounts = os.environ.get("TOKITTY_DEBUG_ACCOUNTS")
    debug = bool(debug_state or debug_accounts == "2")

    # Under the lock and before anything else writes to the state dir, as in
    # run_gui. The walkthrough is Tk's (#112), so the answer is ignored; what
    # matters is that a fresh dir is marked pending, so the next Tk launch
    # shows it. Debug launches mark nothing.
    first_run.classify(state_dir, load_settings(state_dir), excluded=debug)

    accounts = load_accounts(state_dir)
    warning = env_conflict_warning(accounts)
    if warning:
        print(f"tokitty: {warning}", file=sys.stderr)
    settings = load_settings(state_dir)
    usage_state = make_usage_state(settings)
    loaded_theme = theme.load_theme(state_dir)

    app = QApplication.instance() or QApplication([])

    if debug:
        # Nothing below this branch runs: no migration, units, discovery or
        # workers, so a debug launch writes nothing but the lock file.
        count = 2 if debug_accounts == "2" else 1
        from tokitty.menu import build_menu

        def debug_menu(_pane_index: int):
            # Nothing to refresh and no settings to write, but Tk's debug
            # window has a menu, and Exit is the way out of a frameless window.
            return build_menu(
                on_refresh=lambda: None,
                always_on_top=lambda: widget.always_on_top,
                on_toggle_always_on_top=lambda: widget.set_always_on_top(not widget.always_on_top),
                on_quit=app.quit,
            )

        widget = TokittyQtWidget(loaded_theme, [Customization() for _ in range(count)], count,
                                 build_menu_model=debug_menu)
        fake = dict(
            state=debug_state or "content", session_pct=37.0, weekly_pct=62.0,
            session_reset_text="resets 9pm", weekly_reset_text="resets Fri",
            driving_tag="debug", credits_text=None, hint_text=None, dimmed=False,
        )
        for index, pane in enumerate(widget.panes):
            resting = dict(fake, state="sleeping", dimmed=True, hint_text="last seen 17:40")
            pane.render(**(fake if index == 0 else resting))
        widget.restore_position()
        widget.show()
        app.exec()
        return 0

    from tokitty.distro_probe import RunningDistroProbe
    from tokitty.menu import build_menu
    from tokitty.ntfy import Notifier
    from tokitty.wsl_probe import WslCredentialsCache

    # One wsl.exe credential sweep for the whole launch, shared by discovery
    # and the per-account resolvers, and disabled when credentials were
    # already found natively (see run_gui for why).
    env_override_set = bool(os.environ.get("TOKITTY_CREDENTIALS"))
    home_relative_exists = (Path.home() / ".claude" / ".credentials.json").is_file()
    wsl_credentials = WslCredentialsCache(enabled=not (env_override_set or home_relative_exists))
    distro_probe = RunningDistroProbe()

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
    save_customization(state_dir, customization_store)
    mark_customization_migration_complete(state_dir, CUSTOMIZATION_MIGRATION_KEY)
    mark_customization_migration_complete(state_dir, LEGACY_ACCOUNT_LABELS_MIGRATION_KEY)

    notifier = Notifier(state_dir)
    units: list = []
    try:
        units = build_units(
            accounts=accounts, state_dir=state_dir, customization_store=customization_store,
            wsl_credentials=wsl_credentials, distro_probe=distro_probe, usage_state=usage_state,
        )

        def set_view_mode(value: str) -> None:
            usage_state["view"] = value
            update_settings(state_dir, view_mode=value)

        def menu_model(_pane_index: int):
            return build_menu(
                on_refresh=lambda: refresh_units(units),
                always_on_top=lambda: widget.always_on_top,
                on_toggle_always_on_top=lambda: widget.set_always_on_top(not widget.always_on_top),
                on_quit=app.quit,
                view_modes=VIEW_MODE_ITEMS,
                current_view_mode=lambda: usage_state["view"],
                on_view_mode=set_view_mode,
            )

        widget = TokittyQtWidget(
            loaded_theme, [unit["custom"] for unit in units], len(units),
            position_path=state_dir / POSITION_FILENAME, build_menu_model=menu_model,
        )
        for unit, pane in zip(units, widget.panes):
            unit["pane"] = pane
            pane.set_appearance(label=unit["label"])
        widget.restore_position()

        discovery = Discovery(
            state_dir, accounts_state=load_accounts_result(state_dir).state,
            env_override_set=env_override_set, home_relative_exists=home_relative_exists,
            wsl_credentials=wsl_credentials,
        )
        discovery.start()
        maybe_onboard = make_onboarder(state_dir, usage_state, lambda: None)  # no tray to refresh
        notes_removed: set = set()

        def show_warnings(warnings) -> None:
            QMessageBox.warning(widget, "Tokitty", "\n\n".join(warnings))

        def tick() -> None:
            hook_warnings = discovery.take_hook_warnings()
            if hook_warnings:
                # Deferred: the box is modal and must not hold up the tick.
                QTimer.singleShot(0, lambda warnings=hook_warnings: show_warnings(warnings))
            now = datetime.now(timezone.utc)
            for unit in units:
                update = step_unit(
                    unit, usage_state=usage_state, notifier=notifier,
                    customization_store=customization_store, notes_removed=notes_removed,
                    onboard=maybe_onboard, now=now,
                )
                if update is not None:
                    getattr(unit["pane"], "render" if update.kind == "limits" else "render_usage")(
                        **update.kwargs
                    )

        for unit in units:
            unit["poller"].start()
            unit["watcher"].start()
            unit["usage"].start()
        timer = QTimer(widget)
        timer.timeout.connect(tick)
        timer.start(UI_REFRESH_MS)
        # showEvent runs the macOS helper and sets the click mask once the
        # native window exists.
        widget.show()
        app.exec()
        return 0
    finally:
        for unit in units:
            unit["poller"].stop()
            unit["watcher"].stop()
            unit["usage"].stop()
        notifier.stop()
