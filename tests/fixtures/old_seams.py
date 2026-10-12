"""The Settings seam closures exactly as run_gui defined them before they moved
into tokitty.app_controls. Only their free variables became parameters, the
window / messagebox reach-throughs became `panes` and `warn`, and the seams
are returned on a namespace instead of being assigned onto the Tk window.

Parity tests run these against app_controls.make_controls. Frozen on purpose:
do not tidy or share code with the new module.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from types import SimpleNamespace
from typing import Optional

from tokitty import autostart, sprites
from tokitty.app_core import initial_label
from tokitty.customize import load_customization, save_customization_entry
from tokitty.settings import load_settings, update_settings

# Patched by the tests, as they patch the same names on app_controls.
random_look = None
ntfy_send_test = None


def make_old_seams(state_dir, units, usage_state, customization_store, settings, *,
                   apply_customization, deck, panes, warn):
    seams = SimpleNamespace(autostart_enabled=None, on_toggle_autostart=None)

    def handle_customization_changed(pane_index: int, field: str, value: Optional[str]) -> None:
        unit = units[pane_index]
        key = unit["key"]
        # Start from this identity's latest persisted value, not the
        # process-lifetime startup snapshot.  Accounts... may have changed
        # its label (or added other identities) since run_gui initialized.
        custom = load_customization(state_dir).get(key, customization_store[key])

        if field == "colorway":
            if value in sprites.COLORWAYS:
                custom = replace(custom, colorway=value)
        elif field == "pattern":
            if value in sprites.PATTERNS:
                custom = replace(custom, pattern=value)
        elif field == "randomize":
            cw, pat = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            custom = replace(custom, colorway=cw, pattern=pat)
        elif field == "reset":
            custom = replace(custom, overrides={})
        elif field in ("coat_base", "coat_shade", "card_bg", "bar_fill"):
            if value:
                overrides = dict(custom.overrides)
                overrides[field] = value
                custom = replace(custom, overrides=overrides)
        elif field == "label":
            if value is not None:
                custom = replace(custom, label=value)
        else:
            return

        customization_store[key] = custom
        save_customization_entry(state_dir, key, custom)
        apply_customization(unit["pane"], custom)
        if field == "label":
            label = initial_label(unit["account"], custom, unit["tag_kind"])
            unit["pane"].set_appearance(label=label)

    surprise_state = {"on": settings.surprise_me}
    seams.surprise_me = lambda: surprise_state["on"]

    def randomize(pane_index: int) -> None:
        handle_customization_changed(pane_index, "randomize", None)

    def toggle_surprise() -> None:
        surprise_state["on"] = not surprise_state["on"]
        update_settings(state_dir, surprise_me=surprise_state["on"])
        if surprise_state["on"]:
            for i in range(len(units)):
                handle_customization_changed(i, "randomize", None)

    def set_view_mode(value: str) -> None:
        usage_state["view"] = value
        update_settings(state_dir, view_mode=value)

    def set_usage_window(value: str) -> None:
        usage_state["window"] = value
        update_settings(state_dir, usage_window=value)
        # Re-aggregate the records already in memory rather than rescan:
        # switching windows is arithmetic, so the panes update on the next
        # tick with no I/O and no visible gap.
        for unit in units:
            unit["usage"].rebuild_for_window()

    def set_usage_readout(value: str) -> None:
        usage_state["readout"] = value
        update_settings(state_dir, usage_readout=value)

    def budget_for_pane(pane_index: int) -> Optional[float]:
        from tokitty.settings import budget_for

        return budget_for(load_settings(state_dir), units[pane_index]["key"], usage_state["window"])

    def set_budget_for_pane(pane_index: int, text: str) -> Optional[str]:
        """Save (or clear, on blank) one pane's budget for the current usage
        window. Returns an error message, or None on success."""
        from tokitty.settings import parse_budget, with_budget

        amount, error = parse_budget(text)
        if error is not None:
            return error
        budgets = with_budget(load_settings(state_dir), units[pane_index]["key"], usage_state["window"], amount)
        usage_state["budgets"] = budgets
        update_settings(state_dir, usage_budgets=budgets)
        return None

    def toggle_usage_notes() -> None:
        enabled, session_pct, weekly_pct = usage_state["notes"]
        usage_state["notes"] = (not enabled, session_pct, weekly_pct)
        update_settings(state_dir, usage_notes_enabled=not enabled)

    def usage_note_threshold(kind: str) -> int:
        return usage_state["notes"][1 if kind == "session" else 2]

    def set_usage_note_threshold(kind: str, text: str) -> Optional[str]:
        from tokitty.settings import parse_percent

        value, error = parse_percent(text)
        if error is not None:
            return error
        enabled, session_pct, weekly_pct = usage_state["notes"]
        if kind == "session":
            usage_state["notes"] = (enabled, value, weekly_pct)
            update_settings(state_dir, usage_note_session_pct=value)
        else:
            usage_state["notes"] = (enabled, session_pct, value)
            update_settings(state_dir, usage_note_weekly_pct=value)
        return None

    def toggle_ntfy() -> None:
        from tokitty.settings import generate_ntfy_topic

        enabled, url, topic, token = usage_state["ntfy"]
        enabled = not enabled
        changes = {"ntfy_enabled": enabled}
        if enabled and not topic:
            topic = generate_ntfy_topic()
            changes["ntfy_topic"] = topic
        usage_state["ntfy"] = (enabled, url, topic, token)
        update_settings(state_dir, **changes)

    def ntfy_value(field: str) -> str:
        return usage_state["ntfy"][{"url": 1, "topic": 2, "token": 3}[field]]

    def set_ntfy_value(field: str, text: str) -> Optional[str]:
        from tokitty.settings import parse_ntfy_token, parse_ntfy_topic, parse_ntfy_url

        if field == "url":
            value, error = parse_ntfy_url(text)
        elif field == "topic":
            value, error = parse_ntfy_topic(text)
        else:
            value, error = parse_ntfy_token(text)
        if error is not None:
            return error
        saved = update_settings(state_dir, **{"ntfy_" + field: value})
        usage_state["ntfy"] = (saved.ntfy_enabled, saved.ntfy_url, saved.ntfy_topic, saved.ntfy_token)
        return None

    def send_ntfy_test_seam() -> Optional[str]:
        _enabled, url, topic, token = usage_state["ntfy"]
        if not topic:
            return "Enter a topic first."
        return ntfy_send_test(url, topic, token)


    hidden_accounts = list(settings.streamdock_hidden_accounts)

    def streamdock_account_toggles() -> list:
        """(label, is shown, toggle) per account that can be hidden. Read when the menu opens."""
        from tokitty.streamdock.presets import account_label, save_hidden_accounts

        def toggle(name: str) -> None:
            names = [n for n in hidden_accounts if n != name]
            if name not in hidden_accounts:
                names.append(name)
            try:
                saved = save_hidden_accounts(state_dir, names)
            except OSError as exc:
                print(f"tokitty: streamdock: could not save settings: {exc}", file=sys.stderr)
                return
            hidden_accounts[:] = saved
            deck.set_hidden_accounts(saved)

        out = []
        for i, unit in enumerate(units):
            account = unit.get("account")
            if account is None:
                continue
            label = account_label(i, account, panes[i]._label if i < len(panes) else None)
            out.append((label, (lambda n=account.name: n not in hidden_accounts), (lambda n=account.name: toggle(n))))
        return out

    autostart_backend = autostart.get_backend()
    if autostart_backend is not None:
        try:
            autostart.ensure_current(state_dir, autostart_backend)
            autostart_registered = autostart_backend.is_registered()
        except (OSError, ImportError):
            # get_backend() decides purely from sys.platform, so a test (or
            # a genuinely odd install) that reports "win32" without the
            # winreg module actually being importable reaches here rather
            # than at get_backend() itself -- see the sys.platform-spoofing
            # tests in test_main.py (test_run_discovery_survives_wsl_scan_
            # raising_credentials_error and neighbors) that force sys.
            # platform to "win32" on non-Windows CI to exercise unrelated
            # WSL logic. Degrade exactly like get_backend() returning None:
            # leave the seam at its None default instead of crashing
            # startup, mirroring ensure_current's own "never crash startup"
            # OSError guard.
            autostart_registered = None
        if autostart_registered is not None:
            autostart_state = {"enabled": autostart_registered}
            seams.autostart_enabled = lambda: autostart_state["enabled"]

            def toggle_autostart() -> None:
                if autostart_state["enabled"]:
                    autostart_backend.deregister()
                else:
                    try:
                        autostart.write_launcher_and_register(state_dir, autostart_backend)
                    except autostart.AppTranslocatedError as exc:
                        warn("Start at login", str(exc))
                # Refused or not, the checkbox always reflects the
                # backend's real state, never an assumption about what
                # the branch above did.
                autostart_state["enabled"] = autostart_backend.is_registered()

            seams.on_toggle_autostart = toggle_autostart

    seams.on_customization_changed = handle_customization_changed
    seams.on_randomize = randomize
    seams.on_toggle_surprise = toggle_surprise
    seams.on_view_mode = set_view_mode
    seams.on_usage_window = set_usage_window
    seams.on_usage_readout = set_usage_readout
    seams.budget_for_pane = budget_for_pane
    seams.set_budget_for_pane = set_budget_for_pane
    seams.on_toggle_usage_notes = toggle_usage_notes
    seams.usage_note_threshold = usage_note_threshold
    seams.set_usage_note_threshold = set_usage_note_threshold
    seams.on_toggle_ntfy = toggle_ntfy
    seams.ntfy_value = ntfy_value
    seams.set_ntfy_value = set_ntfy_value
    seams.send_ntfy_test = send_ntfy_test_seam
    seams.streamdock_account_toggles = streamdock_account_toggles
    seams.ntfy_enabled = lambda: usage_state["ntfy"][0]
    seams.usage_notes_enabled = lambda: usage_state["notes"][0]
    seams.view_mode = lambda: usage_state["view"]
    seams.usage_window = lambda: usage_state["window"]
    seams.usage_readout = lambda: usage_state["readout"]
    seams.autostart_backend = autostart_backend
    return seams
