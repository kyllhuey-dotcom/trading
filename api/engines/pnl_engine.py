"""v3.3 — PnL & fee accounting for REAL broker fills.

Rules:
- BUY : gross_pnl = (exit_price - entry_price) * filled_quantity
- SELL: gross_pnl = (entry_price - exit_price) * filled_quantity
- net_pnl = gross_pnl - fees
- Fees are accumulated WITHOUT double counting: each fill only accounts its
  own fee portion; ``last_accounted_filled`` guards the partial-fill delta.
- A close without a confirmed exit price must NEVER invent a price: use the
  CLOSED_PRICE_PENDING marker and finalize later (reconciliation).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .protection_state import normalize_order_status

# Accounting state for authoritative closes without a confirmed price.
CLOSED_PRICE_PENDING = "CLOSED_PRICE_PENDING"

# A residual quantity below this absolute floor is treated as fully closed
# (lot tolerance). Per-market lot_size refines this when available.
LOT_TOLERANCE_ABS = 1e-8
LOT_TOLERANCE_REL = 1e-6


def normalize_fill(order: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize a broker order/fill dict to the canonical fill shape."""
    order = order or {}
    fee = order.get("fees") if "fees" in order else order.get("fee") or {}
    if isinstance(fee, dict):
        try:
            fees = float(fee.get("cost") or 0.0)
        except (TypeError, ValueError):
            fees = 0.0
    else:
        try:
            fees = float(fee or 0.0)
        except (TypeError, ValueError):
            fees = 0.0
    try:
        filled = float(order.get("filled") or 0.0)
    except (TypeError, ValueError):
        filled = 0.0
    try:
        average = float(order.get("average") or order.get("price") or 0.0)
    except (TypeError, ValueError):
        average = 0.0
    return {
        "order_id": order.get("id") or order.get("order_id") or order.get("broker_order_id"),
        "client_order_id": order.get("clientOrderId") or order.get("client_order_id"),
        "status": normalize_order_status(order.get("status")),
        "filled": max(0.0, filled),
        "average": average,
        "fees": fees,
        "timestamp": order.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def gross_pnl(direction: str, entry_price: float, exit_price: float,
              filled_quantity: float) -> float:
    """Realized gross PnL for one fill leg. BUY and SELL are symmetric."""
    side = str(direction or "").upper()
    if side == "BUY":
        return (float(exit_price) - float(entry_price)) * float(filled_quantity)
    if side == "SELL":
        return (float(entry_price) - float(exit_price)) * float(filled_quantity)
    raise ValueError(f"Unknown direction: {direction!r}")


def net_pnl(gross: float, fees: float) -> float:
    """net_pnl = gross - fees (fees are always a cost, never negative credit)."""
    return float(gross) - abs(float(fees or 0.0))


def fill_delta(broker_filled: float, last_accounted_filled: float) -> float:
    """Only the POSITIVE delta between the broker's cumulative fill and the
    last accounted amount may be accounted. Reconcile must be idempotent:
    re-running with the same broker_filled yields delta == 0."""
    return max(0.0, float(broker_filled or 0.0) - float(last_accounted_filled or 0.0))


def residual_quantity(original_quantity: float, broker_filled: float,
                      lot_size: Optional[float] = None) -> float:
    """Remaining position quantity after the broker-side fill."""
    residual = float(original_quantity or 0.0) - float(broker_filled or 0.0)
    if residual <= lot_tolerance(lot_size):
        return 0.0
    return residual


def lot_tolerance(lot_size: Optional[float] = None) -> float:
    """Closing tolerance: one exchange lot step (or the absolute floor)."""
    if lot_size:
        return max(float(lot_size), LOT_TOLERANCE_ABS)
    return LOT_TOLERANCE_ABS


def is_fully_closed(residual: float, lot_size: Optional[float] = None) -> bool:
    return float(residual or 0.0) <= lot_tolerance(lot_size)


def fee_portion(total_fees: float, filled: float, total_order_filled: float) -> float:
    """Pro-rata fee share of a partially filled order (linear estimate)."""
    if not total_order_filled:
        return abs(float(total_fees or 0.0))
    ratio = min(1.0, float(filled or 0.0) / float(total_order_filled))
    return abs(float(total_fees or 0.0)) * max(0.0, ratio)
