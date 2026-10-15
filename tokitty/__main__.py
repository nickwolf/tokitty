"""Entry point: python -m tokitty."""
from __future__ import annotations

import sys
from typing import Optional

from tokitty.activity import ActivityTracker
from tokitty.activity_watcher import ActivityWatcher
# Re-exported, not just imported: these were __main__ functions before they
# moved into app_core and are still imported from here by the tests.
from tokitty.app_core import (  # noqa: F401
    _STALE_HINTS,
    _display_state_for,
    _next_last_good,
    _projection_text_for,
    initial_customization,
    initial_label,
    provider_tag_kind,
    publish_ntfy,
    publish_usage_notes,
)
from tokitty.paths import get_state_dir
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

# PySide6 (tokitty.qt.app) is deliberately NOT imported at module level:
# --debug-print and the other CLI flags must keep working on systems
# without it. main() imports run_qt lazily, only when the GUI path runs.

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"


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


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Before anything opens an HTTPS connection, --self-check included.
    from tokitty.tls import use_system_ca_bundle

    use_system_ca_bundle()
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
    from tokitty.qt.app import run_qt

    return run_qt(after_update_token=token, apply_update="--apply-update" in argv)


if __name__ == "__main__":
    sys.exit(main())
