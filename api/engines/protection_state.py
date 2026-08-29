"""v3.3 — SL/TP protection order state machine.

Core invariant: an order ID alone NEVER proves a protection is alive.
Liveness is a function of (normalized status, freshness, consecutive
errors) stored in the trade metadata:

    protection_status           OPEN | PARTIALLY_FILLED | FILLED | CANCELED |
                                EXPIRED | REJECTED | UNKNOWN | NAKED
    protection_checked_at       unix ts of the last successful status check
    protection_error_count      consecutive failed status checks
    sl_order_status / tp_order_status   raw normalized statuses
    filled_protection           "sl" | "tp" when a protection filled
    filled_protection_order_id  the order that filled
    sibling_order_id            the opposite protection order
    sibling_cancel_status       "CANCELED" | "FAILED" | None
    last_accounted_filled       broker-filled qty already accounted in PnL
    sl_tp_failed                legacy flag: protections could not be attached
    protection_uncertain        state could not be determined (unknown)
    protection_cancelled_before_close  cancel ok but hedge failed (NAKED)

Decision table used by the software backstop (index.py) and by
reconciliation (broker_connector.py):

- OPEN confirmed recently            -> ALIVE  (no software backstop)
- PARTIALLY_FILLED (recent)          -> ALIVE  (residual handled by reconcile)
- FILLED                             -> FILLED (position closed on exchange)
- CANCELED / EXPIRED / REJECTED      -> NAKED  (exchange no longer protects)
- None / check errors                -> UNKNOWN (never close DB on this alone;
                                            after MAX_ERRORS the backstop may
                                            act — a reduce-only order cannot
                                            double-hedge a flat position)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

# Normalized statuses (the only values stored in metadata)
OPEN = "OPEN"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
FILLED = "FILLED"
CANCELED = "CANCELED"
EXPIRED = "EXPIRED"
REJECTED = "REJECTED"
UNKNOWN = "UNKNOWN"
NAKED = "NAKED"

VALID_STATUSES = frozenset({
    OPEN, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED, REJECTED, UNKNOWN, NAKED,
})

# Liveness verdicts
ALIVE = "ALIVE"

# A protection whose status was confirmed less than this many seconds ago is
# considered "confirmed recently" (alive without backstop).
DEFAULT_FRESH_WINDOW_S = 90.0

# After this many consecutive status-check errors the protection state is
# declared UNKNOWN and the backstop is allowed to act (reduce-only).
MAX_CONSECUTIVE_ERRORS = 3

_RAW_STATUS_MAP = {
    "OPEN": OPEN, "NEW": OPEN, "PENDING": OPEN, "PENDING_NEW": OPEN,
    "PARTIALLY_FILLED": PARTIALLY_FILLED, "PARTIAL": PARTIALLY_FILLED,
    "FILLED": FILLED, "CLOSED": FILLED,  # CCXT "closed" == fully filled
    "CANCELED": CANCELED, "CANCELLED": CANCELED,
    "EXPIRED": EXPIRED,
    "REJECTED": REJECTED,
}


def normalize_order_status(raw: Any) -> str:
    """Map an exchange/CCXT raw order status onto the normalized vocabulary."""
    if raw is None:
        return UNKNOWN
    mapped = _RAW_STATUS_MAP.get(str(raw).strip().upper())
    return mapped if mapped is not None else UNKNOWN


def protection_liveness(meta: Optional[Dict[str, Any]], now: Optional[float] = None,
                        fresh_window_s: float = DEFAULT_FRESH_WINDOW_S,
                        max_errors: int = MAX_CONSECUTIVE_ERRORS) -> str:
    """Verdict: ALIVE / FILLED / NAKED / UNKNOWN.

    Never returns ALIVE on the mere presence of an order ID: the status must
    be OPEN/PARTIALLY_FILLED AND confirmed within ``fresh_window_s``.
    """
    meta = meta or {}
    if now is None:
        now = time.time()
    status = str(meta.get("protection_status") or "").upper()
    # Explicit legacy flag from v3.1: attach failed at open time.
    if bool(meta.get("sl_tp_failed")) or status == NAKED:
        return NAKED
    if status in (CANCELED, EXPIRED, REJECTED):
        return NAKED
    if status == FILLED:
        return FILLED
    if status in (OPEN, PARTIALLY_FILLED):
        checked_at = float(meta.get("protection_checked_at") or 0)
        if checked_at and (now - checked_at) <= fresh_window_s:
            return ALIVE
        # Not confirmed recently: an ID alone does not keep blocking the
        # backstop indefinitely — fall through to the error/stale logic.
    errors = int(meta.get("protection_error_count") or 0)
    if errors >= max_errors:
        return UNKNOWN
    if not status:
        # Never checked / no recorded status.
        return UNKNOWN
    return UNKNOWN


def has_any_protection(meta: Optional[Dict[str, Any]]) -> bool:
    meta = meta or {}
    return bool(meta.get("sl_order_id") or meta.get("tp_order_id")
                or meta.get("sl_order_status") or meta.get("tp_order_status"))


def is_naked(meta: Optional[Dict[str, Any]]) -> bool:
    """True when the position has lost (or never had) exchange protection."""
    meta = meta or {}
    if bool(meta.get("sl_tp_failed")):
        return True
    status = str(meta.get("protection_status") or "").upper()
    if status == NAKED:
        return True
    return not has_any_protection(meta)


def backstop_allowed(meta: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
    """May the software backstop send a reduce-only close for this trade?

    The backstop is safe in every NAKED/UNKNOWN/stale case because the close
    order is reduce-only: if the exchange protection is still alive and fills
    first, the backstop order is simply rejected (no double hedge).
    """
    return protection_liveness(meta, now) != ALIVE
