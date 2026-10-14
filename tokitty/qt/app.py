"""The GUI entry point: run_qt.

The unit construction, the tick step, onboarding and discovery all come from
app_core, so what a pane shows is decided in one place. The update check and
the installer (#113) and the tray (#114; a debug launch has none) are wired
here. PySide6 is imported inside run_qt, so importing this module never needs
it.
"""
from __future__ import annotations

import json
import os
import sys
import time
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
from tokitty.usage_items import VIEW_MODE_ITEMS  # noqa: F401  (also read by the tests)

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
DEBUG_NOTIFY_ENV = "TOKITTY_DEBUG_NOTIFY"
DEBUG_NOTIFY_DELAY_MS = 5000
AFTER_UPDATE_QT_FILE = "after-update-qt.json"
POSITION_FILENAME = "position.json"
MISSING_PYSIDE6 = (
    'tokitty: PySide6 is not installed; reinstall tokitty with its dependencies '
    '(pip install -e ".") or pip install PySide6-Essentials'
)


def apply_customization(pane, custom) -> None:
    """Restyle a pane from its customization. An empty string means no
    override."""
    from tokitty.customize import effective_palette

    pane.set_appearance(
        palette=effective_palette(custom),
        card_bg=custom.overrides.get("card_bg", ""),
        bar_fill=custom.overrides.get("bar_fill", ""),
        colorway=custom.colorway, pattern=custom.pattern,
    )


class AnnouncerTray:
    """What `announcer` needs of a tray. A refresh goes through the host, so
    the tray menu and an open Settings pill both re-sync; notify is the
    tray's, and a decline leaves notified_tag unwritten so the release is
    announced on a later run."""

    def __init__(self, host, tray):
        self._host = host
        self._tray = tray

    def refresh(self) -> None:
        self._host.notify_state_changed()

    def notify(self, title: str, message: str) -> bool:
        return self._tray.notify(title, message)


def show_about(frozen, parent, about_panel=None, platform=sys.platform):
    """Answer About Tokitty. A frozen macOS build has a real Info.plist, so
    the native panel shows its name, version and copyright; a source run would
    get Python's, so it shows the version in a plain box instead. Other
    platforms have no native panel, so a frozen build there gets the box too,
    as does a macOS build whose panel fails, without the source line. The app
    is activated first: an agent app does not come forward on its own."""
    from PySide6.QtWidgets import QMessageBox

    from tokitty.qt import macos

    macos.activate_app()
    if frozen and platform == "darwin":
        try:
            if about_panel is None:
                about_panel = macos.show_about_panel
            about_panel()
            return
        except Exception as exc:
            print(f"tokitty: about panel: {exc}", file=sys.stderr)
    from tokitty.app_version import version_text

    text = f"Tokitty {version_text()}" if frozen else f"Tokitty {version_text()}\nRunning from source."
    QMessageBox.information(parent, "About Tokitty", text)


def qt_timers(parent):
    """The `after` and `cancel` callables DeckStarter takes, over single-shot
    QTimers on the GUI thread. after(ms, fn) returns the timer; cancel stops
    it. A timer deletes itself once it has fired."""
    from PySide6.QtCore import QTimer

    def after(ms, fn):
        timer = QTimer(parent)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.timeout.connect(timer.deleteLater)
        timer.start(ms)
        return timer

    def cancel(timer):
        try:
            timer.stop()
        except RuntimeError:  # already fired and deleted
            pass

    return after, cancel


def run_guarded(label, fn) -> None:
    """Run one shutdown step so its failure is reported, not raised: it must
    neither hide the exception that is already unwinding nor skip the steps
    after it."""
    try:
        fn()
    except Exception as exc:
        print(f"tokitty: shutdown: {label}: {exc}", file=sys.stderr)


def run_walkthrough(state_dir, credentials_cache, distro_probe) -> bool:
    """Show the first-run walkthrough and wait for it in a local event loop
    that its `closed` signal quits. True when the launch was asked to quit,
    by the walkthrough's Quit shortcut or a Qt Quit event, both of which leave
    onboarding pending: QApplication.quit() does
    nothing before app.exec() has started, so the caller has to stop the
    launch itself. Otherwise the caller reloads accounts and settings."""
    from PySide6.QtCore import QEvent, QEventLoop, QObject
    from PySide6.QtWidgets import QApplication

    from tokitty.qt import macos
    from tokitty.qt.walkthrough import Walkthrough

    loop = QEventLoop()
    done = {"closed": False, "quit": False}
    shown = {}

    class QuitWatch(QObject):
        # Qt would answer a Quit event (Command-Q, logout) by closing every
        # window, and the walkthrough's close means Skip, which marks
        # onboarding done. So the event goes to the walkthrough's own Quit,
        # which leaves it pending, or is refused with it while an install runs.
        def eventFilter(self, obj, event):
            if obj is QApplication.instance() and event.type() == QEvent.Type.Quit and "walk" in shown:
                shown["walk"].quit_app()
                return True
            return False

    def on_closed():
        done.update(closed=True)
        loop.quit()

    def on_quit_requested():
        done.update(quit=True)
        loop.quit()

    app = QApplication.instance()
    watch = QuitWatch()
    app.installEventFilter(watch)
    try:
        walkthrough = Walkthrough(state_dir, credentials_cache, distro_probe.get_running)
        walkthrough.closed.connect(on_closed)
        walkthrough.quit_requested.connect(on_quit_requested)
        shown["walk"] = walkthrough
        walkthrough.show()
        macos.activate_app()
        walkthrough.raise_()
        walkthrough.activateWindow()
        if not (done["closed"] or done["quit"]):
            loop.exec()
    finally:
        app.removeEventFilter(watch)
    return done["quit"]


def run_qt(after_update_token=None, apply_update=False) -> int:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(MISSING_PYSIDE6, file=sys.stderr)
        return 1

    from tokitty import update_controller
    from tokitty.lock import LockAcquisitionError, SingleInstanceLock, acquire_with_retry
    from tokitty.paths import get_state_dir

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
    try:
        if after_update_token and update_controller.skips_ack(os.environ):
            return 0
        return _run_locked(state_dir, lock, after_update_token, apply_update)
    finally:
        lock.release()


def _run_locked(state_dir, lock, after_update_token=None, apply_update=False) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from tokitty import first_run, frozen, theme
    from tokitty.accounts import env_conflict_warning, load_accounts, load_accounts_result
    from tokitty.customize import Customization, load_customization, save_customization
    from tokitty.settings import load_settings, update_settings
    from tokitty.app_controls import make_controls
    from tokitty.distro_probe import RunningDistroProbe
    from tokitty.streamdock.gui_state import StreamdockHolder
    from tokitty.streamdock.install import install_supported, refresh_streamdock_plugin
    from tokitty.streamdock.render import register_font
    from tokitty.streamdock.wiring import (
        BIND_MARGIN_S, DEFAULT_NAME, DeckStarter, apply_idle_cap, gather_inputs,
    )
    from tokitty.streamdock.window import InWindowViews
    from tokitty import update_controller
    from tokitty.update_check import UpdateChecker, announcer
    from tokitty.update_flow import UpdateActions
    from tokitty.qt.deck_views import qt_view_factory
    from tokitty.qt.host import QtHost
    from tokitty.qt.settings import SettingsWindow
    from tokitty.qt.tray import QtTray
    from tokitty.qt.update_dialog import QtUpdateDialog
    from tokitty.qt.update_host import QtUpdateHost
    from tokitty.qt.widget import TokittyQtWidget
    from tokitty.wsl_probe import WslCredentialsCache

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    debug_accounts = os.environ.get("TOKITTY_DEBUG_ACCOUNTS")
    debug = bool(debug_state or debug_accounts == "2")

    # Under the lock and before anything else writes to the state dir, as in
    # run_gui. A fresh dir is marked pending here, so a walkthrough killed
    # midway shows again. Debug launches and the update hand-offs mark nothing
    # and never show it.
    show = first_run.classify(
        state_dir, load_settings(state_dir),
        excluded=debug or bool(after_update_token or apply_update),
    )

    frozen.qt_platform_env()
    app = QApplication.instance() or QApplication([])
    # Quitting is always explicit (Exit, or the widget's closeEvent): the
    # widget is a Tool window that Qt does not count, so with the default on,
    # closing Settings would end the application. Set before the walkthrough,
    # whose close would otherwise be the last window closing.
    app.setQuitOnLastWindowClosed(False)

    # One wsl.exe credential sweep for the whole launch, shared by the
    # walkthrough, discovery and the per-account resolvers, and disabled when
    # credentials were already found natively (see run_gui for why). The
    # walkthrough lists every install, so it needs the sweeps regardless.
    env_override_set = bool(os.environ.get("TOKITTY_CREDENTIALS"))
    home_relative_exists = (Path.home() / ".claude" / ".credentials.json").is_file()
    wsl_credentials = WslCredentialsCache(enabled=show or not (env_override_set or home_relative_exists))
    distro_probe = RunningDistroProbe()

    if show:
        # The panes are built once from accounts.json, so the walkthrough
        # runs first. Nothing else starts until it closes: no pollers, units
        # or discovery, which also keeps the hook guard free for its installs.
        if run_walkthrough(state_dir, wsl_credentials, distro_probe):
            # Quit while it was open: nothing else of the launch runs. The
            # Quit shortcut leaves onboarding pending; a Qt Quit event closes
            # it as its close button would.
            return 0
        if env_override_set or home_relative_exists:
            # The sweep was only for listing installs. With native credentials
            # the rest of the launch keeps the usual guard, or a skipped
            # walkthrough would read activity from a WSL install while the
            # limits come from this PC.
            wsl_credentials = WslCredentialsCache(enabled=False)

    accounts = load_accounts(state_dir)
    warning = env_conflict_warning(accounts)
    if warning:
        print(f"tokitty: {warning}", file=sys.stderr)
    settings = load_settings(state_dir)
    usage_state = make_usage_state(settings)
    loaded_theme = theme.load_theme(state_dir)

    if debug:
        # Nothing below this branch runs: no migration, units, discovery or
        # workers, so a debug launch writes nothing but the lock file.
        count = 2 if debug_accounts == "2" else 1
        from tokitty.menu import build_menu

        def about() -> None:
            show_about(bool(getattr(sys, "frozen", False)), widget)

        def debug_menu(_pane_index: int):
            # Nothing to refresh and no settings to write, but Exit is the way
            # out of a frameless window.
            return build_menu(
                on_refresh=lambda: None,
                always_on_top=lambda: widget.always_on_top,
                on_toggle_always_on_top=lambda: widget.set_always_on_top(not widget.always_on_top),
                on_about=about,
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

    from tokitty.menu import build_menu
    from tokitty.ntfy import Notifier

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
    # Set below, once the panes exist. The finally releases whichever got that
    # far, so a failure in setup still closes what was opened.
    deck = None
    deck_views = None
    tray = None
    try:
        units = build_units(
            accounts=accounts, state_dir=state_dir, customization_store=customization_store,
            wsl_credentials=wsl_credentials, distro_probe=distro_probe, usage_state=usage_state,
        )

        def menu_model(pane_index: int):
            return build_menu(
                on_refresh=lambda: refresh_units(units),
                always_on_top=host.always_on_top,
                on_toggle_always_on_top=host.toggle_always_on_top,
                on_quit=app.quit,
                on_open_settings=lambda: SettingsWindow.open(host, pane_index),
                on_about=lambda: show_about(bool(getattr(sys, "frozen", False)), host.dialog_parent()),
                view_modes=VIEW_MODE_ITEMS,
                current_view_mode=lambda: usage_state["view"],
                on_view_mode=lambda value: controls.on_view_mode(value),
                update_available_label=host.update_available_label,
                on_install_update=host.on_install_update,
            )

        widget = TokittyQtWidget(
            loaded_theme, [unit["custom"] for unit in units], len(units),
            position_path=state_dir / POSITION_FILENAME, build_menu_model=menu_model,
        )
        def pane_label(i):
            return widget.panes[i]._label if i < len(widget.panes) else None

        # Two phases: Controls needs host methods and the host needs Controls.
        host = QtHost(widget, state_dir)
        controls = make_controls(
            state_dir, units, usage_state, customization_store, settings,
            apply_customization=apply_customization,
            on_layout_changed=host.on_layout_changed,
            distro_probe=distro_probe,
            # The deck is built below, once the panes exist.
            deck_fn=lambda: deck,
            label_fn=pane_label,
            warn=host.warn,
        )
        host.attach(controls)
        widget.on_menu_action_done = host.notify_state_changed
        for unit, pane in zip(units, widget.panes):
            unit["pane"] = pane
            pane.set_appearance(label=unit["label"])
        if settings.surprise_me:
            for index in range(len(units)):
                controls.on_customization_changed(index, "randomize", None)
        # After the randomizing, so the icon wears pane 0's final colours.
        pane0 = widget.panes[0]
        tray = QtTray(lambda: menu_model(0), state_dir,
                      colorway=pane0._colorway, pattern=pane0._pattern,
                      on_action_done=host.notify_state_changed)
        host.tray_refresh = tray.refresh
        # stop() schedules deleteLater, which Qt never processes once the loop
        # has ended; the finally's stop is the backstop for a failed launch.
        app.aboutToQuit.connect(tray.stop)

        # One source for the tray setting: settings.json, which set_enabled
        # writes. The Settings toggle and the update handover's restore both
        # read it, so a rolled-back handover brings back the tray the user
        # last chose. Availability is read live from the tray, which drops it
        # if start() fails.
        def tray_enabled() -> bool:
            return load_settings(state_dir).tray_enabled

        host.tray_enabled = tray_enabled
        host.tray_available = lambda: tray.available
        host.on_toggle_tray = lambda: tray.set_enabled(not tray_enabled())

        update_host = QtUpdateHost(app, host, lock, state_dir, controls.autostart_backend,
                                   tray=tray, tray_enabled=tray_enabled, quiet=apply_update)
        updates = update_controller.UpdateController(state_dir, update_host)

        # Plain-Python shadow state again, as in run_gui.
        update_check_state = {"on": settings.update_check}
        checker = UpdateChecker(
            state_dir, updates.running, enabled=lambda: update_check_state["on"],
            on_available=announcer(state_dir, AnnouncerTray(host, tray)),
        )

        def toggle_update_check() -> None:
            update_check_state["on"] = not update_check_state["on"]
            update_settings(state_dir, update_check=update_check_state["on"])

        def update_box(show_box):
            return lambda title, text: show_box(host.dialog_parent(), title, text)

        update_actions = UpdateActions(
            checker, updates,
            # Held while a handover has the app hidden, so a check result
            # can't bring the dialog back over a copy that is handing over.
            defer=lambda fn: QTimer.singleShot(0, lambda: update_host.when_shown(fn)),
            error=update_box(QMessageBox.critical),
            info=update_box(QMessageBox.information),
            offer=lambda release: QtUpdateDialog.open(host, release, updates.running.version, updates),
        )
        host.update_available_label = checker.menu_label
        host.on_install_update = update_actions.install
        host.on_check_updates = update_actions.check_now
        host.update_check_enabled = lambda: update_check_state["on"]
        host.on_toggle_update_check = toggle_update_check
        apply_exit = {"code": 1}
        widget.restore_position()
        def account_name(index: int) -> str:
            account = units[index]["account"]
            return account.name if account else DEFAULT_NAME

        try:
            register_font()
        except Exception as exc:
            # The keys then raise per request in the deck server's handler
            # threads, which the server survives; the app keeps running.
            print(f"tokitty: streamdock: key font: {exc}", file=sys.stderr)

        deck_views = InWindowViews(lambda: deck.runtime, account_name, widget.pane_anchor,
                                   qt_view_factory())
        # A busy deck port is retried on the GUI thread for as long as an
        # update's handover can take, so a new copy launched by the updater
        # still gets it.
        deck_after, deck_cancel = qt_timers(widget)
        deck = DeckStarter(
            settings, units, distro_probe.get_running,
            palette_fn=lambda i: units[i]["pane"].palette,
            open_in_window=deck_views.open, after=deck_after, cancel=deck_cancel,
            deadline_s=update_controller.ACK_TIMEOUT + BIND_MARGIN_S,
        )
        deck.begin()
        refresh_streamdock_plugin(state_dir)

        # The Settings tab updates this after an install or uninstall; the
        # state is read from it, not from the startup `settings`.
        streamdock_holder = StreamdockHolder(settings, state_dir, lambda: deck.runtime, deck=deck)
        host.streamdock_state = streamdock_holder.state
        host.streamdock_info = streamdock_holder.info
        host.streamdock_install = streamdock_holder.install
        host.streamdock_uninstall = streamdock_holder.uninstall
        host.streamdock_install_supported = install_supported

        def edit_streamdock_presets() -> None:
            from tokitty.qt.presets import PresetsDialog

            PresetsDialog.open(
                state_dir, lambda: [u.get("account") for u in units], pane_label,
                lambda presets: deck.set_presets(presets), host=host,
            )

        host.on_edit_streamdock_presets = edit_streamdock_presets

        discovery = Discovery(
            state_dir, accounts_state=load_accounts_result(state_dir).state,
            env_override_set=env_override_set, home_relative_exists=home_relative_exists,
            wsl_credentials=wsl_credentials,
        )
        discovery.start()

        def open_accounts():
            from tokitty.qt.accounts import AccountsManager

            return AccountsManager.open(state_dir, discovery.wsl_matches(), host=host)

        host.on_open_accounts = open_accounts
        maybe_onboard = make_onboarder(state_dir, usage_state, tray.refresh)
        notes_removed: set = set()

        def show_warnings(warnings) -> None:
            QMessageBox.warning(widget, "Tokitty", "\n\n".join(warnings))

        def tick() -> None:
            updates.tick()
            checker.tick()
            # Not while the handover has the app hidden: a box would open over
            # nothing. The warnings wait in discovery until restore.
            hook_warnings = [] if update_host.hidden else discovery.take_hook_warnings()
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
            streamdock = deck.runtime
            if streamdock is not None:
                try:
                    streamdock.tick(*gather_inputs(units))
                    apply_idle_cap(units, streamdock.connected)
                    deck_views.sync()
                except Exception as exc:
                    print(f"tokitty: streamdock: tick: {exc}", file=sys.stderr)
            interval = 100 if updates.busy else UI_REFRESH_MS
            if timer.interval() != interval:
                timer.setInterval(interval)

        for unit in units:
            unit["poller"].start()
            unit["watcher"].start()
            unit["usage"].start()
        timer = QTimer(widget)
        timer.timeout.connect(tick)
        timer.start(UI_REFRESH_MS)
        if tray.available and settings.tray_enabled:
            tray.start()
        if os.environ.get(DEBUG_NOTIFY_ENV) == "1":
            QTimer.singleShot(DEBUG_NOTIFY_DELAY_MS, lambda: tray.debug_notify(
                "Tokitty", "Test notification (TOKITTY_DEBUG_NOTIFY)"))
        updates.startup(frozen=bool(getattr(sys, "frozen", False)))
        if after_update_token:
            # Inside the event loop and once the widget is exposed, so the
            # first window is up when the old copy hears from us. Not forever:
            # a platform that never exposes must not strand the handover.
            started = time.monotonic()
            ack_timer = QTimer(widget)

            def send_ack() -> None:
                handle = widget.windowHandle()
                exposed = handle is not None and handle.isExposed()
                waited = time.monotonic() - started
                if not exposed and waited < update_controller.ACK_MAP_TIMEOUT:
                    return
                ack_timer.stop()
                from tokitty.update_swap import write_ack

                if os.environ.get(update_controller.API_URL_ENV):
                    # For the rehearsal: the ack is written either way, so
                    # this is the record that a window was up before it.
                    try:
                        (Path(state_dir) / AFTER_UPDATE_QT_FILE).write_text(
                            json.dumps({"exposed_before_ack": exposed, "waited_s": waited}),
                            encoding="utf-8",
                        )
                    except OSError as exc:
                        print(f"tokitty: update: writing {AFTER_UPDATE_QT_FILE}: {exc}", file=sys.stderr)
                try:
                    write_ack(state_dir, after_update_token)
                except OSError as exc:
                    print(f"tokitty: update: writing the ack: {exc}", file=sys.stderr)

            ack_timer.timeout.connect(send_ack)
            ack_timer.start(update_controller.ACK_MAP_POLL_MS)
        if apply_update:
            def on_apply_done(result) -> None:
                apply_exit["code"] = {"installed": 0, "rolled_back": 2}.get(result.outcome, 1)
                if result.outcome != "installed":
                    print(f"tokitty: update: {result.message}", file=sys.stderr)
                    update_host.finish()

            updates.start_apply_newest(on_apply_done)
        # showEvent runs the macOS helper and sets the click mask once the
        # native window exists.
        widget.show()
        app.exec()
        return apply_exit["code"] if apply_update else 0
    finally:
        if tray is not None:
            run_guarded("stop tray", tray.stop)
        if deck_views is not None:
            run_guarded("close deck views", deck_views.close_all)
        if deck is not None:
            run_guarded("stop deck", deck.stop)
        # Each stop on its own, as the deck's are: a partly started unit can
        # raise here (joining a thread that never started), and that must not
        # skip the rest or hide the exception already unwinding.
        for unit in units:
            for part in ("poller", "watcher", "usage"):
                run_guarded(f"stop {part}", unit[part].stop)
        run_guarded("stop notifier", notifier.stop)
