"""
Exchange-aware order normalization (LOT E).

Safe decimal rounding helpers and constraint application so orders respect
each instrument's lot_size / tick_size / min_notional:

- quantity is ALWAYS rounded DOWN to the lot step (never up — rounding up
  would increase risk beyond the computed sizing);
- prices are rounded to the tick step;
- SL/TP are rounded in the *protective* direction (SL away from entry,
  TP toward entry) so a rounded stop never lies inside the intended risk;
- minimum notional violations are rejected with a precise reason.

Compatible with the static per-instrument constraints of MarketUniverse
(tick_size / lot_size / min_order) and with constraints parsed from CCXT
`markets` structures (`precision.amount` / `precision.price` / `limits.cost.min`).
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import math
from typing import Any, Dict, Optional


# --------------------------------------------------------------------------- #
# Decimal-safe rounding primitives                                            #
# --------------------------------------------------------------------------- #
def _dec(value: Any) -> Decimal:
    return Decimal(str(float(value)))


def floor_to_step(value: float, step: float) -> float:
    """Round DOWN to the nearest multiple of `step` (float-safe, never rounds up)."""
    if not step or step <= 0:
        return float(value)
    try:
        q = (_dec(value) / _dec(step)).to_integral_value(rounding=ROUND_FLOOR)
        return float(q * _dec(step))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return float(value)


def ceil_to_step(value: float, step: float) -> float:
    """Round UP to the nearest multiple of `step` (float-safe, never rounds down)."""
    if not step or step <= 0:
        return float(value)
    try:
        q = (_dec(value) / _dec(step)).to_integral_value(rounding=ROUND_CEILING)
        return float(q * _dec(step))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return float(value)


def round_to_tick(value: float, tick: float) -> float:
    """Round to the nearest price tick (half-up, decimal-safe)."""
    if not tick or tick <= 0:
        return float(value)
    try:
        q = (_dec(value) / _dec(tick)).to_integral_value(rounding=ROUND_HALF_UP)
        return float(q * _dec(tick))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return float(value)


def round_protective(value: Optional[float], tick: float, direction: str) -> Optional[float]:
    """
    Protective rounding for SL/TP:
    - BUY : floored → SL moves further below entry (more margin), TP moves
      toward entry (conservative target);
    - SELL: ceiled → symmetric.
    """
    if value is None:
        return None
    if direction == "BUY":
        return floor_to_step(value, tick)
    return ceil_to_step(value, tick)


# --------------------------------------------------------------------------- #
# Constraint extraction                                                       #
# --------------------------------------------------------------------------- #
def constraints_from_info(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Optional[float]]]:
    """Read lot/tick/min-notional from a MarketUniverse instrument info dict."""
    if not info:
        return None
    lot = info.get("lot_size")
    tick = info.get("tick_size")
    min_notional = info.get("min_order")
    try:
        lot = float(lot) if lot else None
        tick = float(tick) if tick else None
        min_notional = float(min_notional) if min_notional else None
    except (TypeError, ValueError):
        lot = tick = min_notional = None
    if not any((lot, tick, min_notional)):
        return None
    return {"lot_size": lot, "tick_size": tick, "min_notional": min_notional}


def parse_ccxt_market_constraints(market: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """
    Extract lot_size / tick_size / min_notional from a CCXT market structure
    (as loaded by `exchange.load_markets()`). Pure function — unit-testable
    without any network access.
    """
    if not market:
        return {"lot_size": None, "tick_size": None, "min_notional": None}
    precision = market.get("precision") or {}
    limits = market.get("limits") or {}

    lot = precision.get("amount")
    tick = precision.get("price")
    min_notional = (limits.get("cost") or {}).get("min")

    def _f(v: Any) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {"lot_size": _f(lot), "tick_size": _f(tick), "min_notional": _f(min_notional)}


# --------------------------------------------------------------------------- #
# Order normalization                                                         #
# --------------------------------------------------------------------------- #
def normalize_order(quantity: float, entry: float, direction: str = "BUY",
                    sl: Optional[float] = None, tp: Optional[float] = None,
                    info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Apply instrument constraints to a candidate order.

    Returns a dict with the (possibly adjusted) quantity/entry/sl/tp, the
    resulting notional, `allowed`/`reason`, and an `adjustments` trail.
    When the instrument has no constraints, values pass through untouched.
    """
    constraints = constraints_from_info(info)
    normalized_direction = str(direction or "").upper()
    try:
        quantity_f = float(quantity)
        entry_f = float(entry)
        sl_f = float(sl) if sl is not None else None
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError, OverflowError):
        return {
            "quantity": 0.0, "entry": 0.0, "sl": None, "tp": None,
            "notional": 0.0, "allowed": False,
            "reason": "Order values must be numeric", "adjusted": False,
            "adjustments": [], "constraints": constraints,
        }
    result: Dict[str, Any] = {
        "quantity": quantity_f,
        "entry": entry_f,
        "sl": sl_f,
        "tp": tp_f,
        "notional": quantity_f * entry_f,
        "allowed": True,
        "reason": None,
        "adjusted": False,
        "adjustments": [],
        "constraints": constraints,
    }
    values = [quantity_f, entry_f, *(v for v in (sl_f, tp_f) if v is not None)]
    if not all(math.isfinite(value) for value in values):
        result.update(allowed=False, reason="Order values must be finite")
        return result
    if normalized_direction not in {"BUY", "SELL"}:
        result.update(allowed=False, reason="direction must be BUY or SELL")
        return result
    if quantity_f <= 0:
        result.update(allowed=False, reason="Quantity must be positive")
        return result
    if entry_f <= 0 or any(value is not None and value <= 0 for value in (sl_f, tp_f)):
        result.update(allowed=False, reason="Order prices must be positive")
        return result
    if sl_f is not None and (
        (normalized_direction == "BUY" and sl_f >= entry_f)
        or (normalized_direction == "SELL" and sl_f <= entry_f)
    ):
        result.update(allowed=False, reason="Stop loss is on the wrong side of entry")
        return result
    if tp_f is not None and (
        (normalized_direction == "BUY" and tp_f <= entry_f)
        or (normalized_direction == "SELL" and tp_f >= entry_f)
    ):
        result.update(allowed=False, reason="Take profit is on the wrong side of entry")
        return result
    if constraints is None:
        return result

    lot, tick, min_notional = constraints["lot_size"], constraints["tick_size"], constraints["min_notional"]
    qty = result["quantity"]

    # 1. Quantity: floor to lot size (never exceed the intended size)
    if lot and lot > 0:
        rounded = floor_to_step(qty, lot)
        if rounded != qty:
            qty = rounded
            result["adjusted"] = True
            result["adjustments"].append("quantity_floored_to_lot")

    # 2. Prices: entry to nearest tick, SL/TP protective
    if tick and tick > 0:
        entry2 = round_to_tick(result["entry"], tick)
        if entry2 != result["entry"]:
            result["entry"] = entry2
            result["adjustments"].append("entry_rounded_to_tick")
            result["adjusted"] = True
        for field in ("sl", "tp"):
            value = result[field]
            if value is None:
                continue
            adjusted = round_protective(value, tick, normalized_direction)
            if adjusted != value:
                result[field] = adjusted
                result["adjusted"] = True
                result["adjustments"].append(f"{field}_rounded_protective")

    result["quantity"] = qty
    result["notional"] = qty * result["entry"]

    # 3. Hard gates (including post-rounding SL/TP geometry).
    rounded_sl, rounded_tp, rounded_entry = result["sl"], result["tp"], result["entry"]
    invalid_sl = rounded_sl is not None and (
        (normalized_direction == "BUY" and rounded_sl >= rounded_entry)
        or (normalized_direction == "SELL" and rounded_sl <= rounded_entry)
    )
    invalid_tp = rounded_tp is not None and (
        (normalized_direction == "BUY" and rounded_tp <= rounded_entry)
        or (normalized_direction == "SELL" and rounded_tp >= rounded_entry)
    )
    if invalid_sl:
        result["allowed"] = False
        result["reason"] = "Stop loss is on the wrong side of entry after rounding"
    elif invalid_tp:
        result["allowed"] = False
        result["reason"] = "Take profit is on the wrong side of entry after rounding"
    elif qty <= 0:
        result["allowed"] = False
        result["reason"] = "Quantity rounds to zero (below minimum lot size)"
    elif min_notional and min_notional > 0 and result["notional"] < min_notional:
        result["allowed"] = False
        result["reason"] = f"Order notional {result['notional']:.4f} below instrument minimum ({min_notional})"

    return result
