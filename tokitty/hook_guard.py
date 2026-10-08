"""Process-wide, state_dir-keyed guard serializing hook-journal writers.

Everything that may write hooks or the single-slot pending-op journal
(the Accounts dialog, the Settings Accounts tab, the startup retry) claims
this slot first, so a closed-then-reopened window cannot race a worker
still running for the old one. Kept in its own module so hooks_install
callers and accounts_ui can both import it without a cycle.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


@dataclass
class InFlightOperation:
    kind: str
    lock: threading.Lock
    state: Dict[str, object]


_in_flight_operations: Dict[Path, InFlightOperation] = {}
_in_flight_operations_lock = threading.Lock()


def state_dir_key(state_dir: Path) -> Path:
    return Path(state_dir).resolve()


def claim_operation(
    state_dir: Path, kind: str
) -> Tuple[Path, InFlightOperation, bool]:
    """Claim the single process-wide hook-operation slot for state_dir.

    Returns (key, operation, claimed). When claimed is False, operation is
    the one already holding the slot.
    """
    key = state_dir_key(state_dir)
    with _in_flight_operations_lock:
        existing = _in_flight_operations.get(key)
        if existing is not None:
            return key, existing, False
        operation = InFlightOperation(
            kind=kind,
            lock=threading.Lock(),
            state={"done": False},
        )
        _in_flight_operations[key] = operation
        return key, operation, True


def complete_operation(
    key: Path, operation: InFlightOperation, outcome: object
) -> None:
    """Publish an outcome and release the shared slot from the worker."""
    with operation.lock:
        if operation.state["done"]:
            return
        operation.state["outcome"] = outcome
        operation.state["done"] = True
    with _in_flight_operations_lock:
        if _in_flight_operations.get(key) is operation:
            _in_flight_operations.pop(key)


# Token-style API for callers that do not need to watch the outcome.
GuardToken = Tuple[Path, InFlightOperation]


def try_acquire(state_dir: Path, label: str) -> Optional[GuardToken]:
    """Non-blocking claim. Returns a token, or None if the slot is held."""
    key, operation, claimed = claim_operation(state_dir, label)
    if not claimed:
        return None
    return key, operation


def release(token: GuardToken) -> None:
    key, operation = token
    complete_operation(key, operation, None)


def held(state_dir: Path) -> bool:
    key = state_dir_key(state_dir)
    with _in_flight_operations_lock:
        return key in _in_flight_operations


def held_kind(state_dir: Path) -> Optional[str]:
    """The label of the operation holding the slot, if any."""
    key = state_dir_key(state_dir)
    with _in_flight_operations_lock:
        op = _in_flight_operations.get(key)
        return op.kind if op is not None else None
