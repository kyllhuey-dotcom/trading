"""Order-type helpers for the institutional trade terminal (LOT 3)."""
from typing import Any, Dict, Optional


def normalize_order_type(raw: Any) -> str:
    t = str(raw or "MARKET").upper().strip()
    if t in ("LIMIT", "STOP", "MARKET"):
        return t
    return "MARKET"


def should_fill_now(order_type: str, direction: str, last: float,
                    limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> bool:
    ot = normalize_order_type(order_type)
    d = str(direction or "BUY").upper()
    try:
        last = float(last)
    except (TypeError, ValueError):
        return False
    if ot == "MARKET":
        return True
    if ot == "LIMIT":
        if limit_price is None:
            return False
        lp = float(limit_price)
        if d == "BUY":
            return last <= lp
        return last >= lp
    if ot == "STOP":
        if stop_price is None:
            return False
        sp = float(stop_price)
        if d == "BUY":
            return last >= sp
        return last <= sp
    return False


def risk_based_quantity(balance: float, max_risk_pct: float, entry: float, sl: float) -> float:
    try:
        balance = float(balance)
        max_risk_pct = float(max_risk_pct)
        entry = float(entry)
        sl = float(sl)
    except (TypeError, ValueError):
        return 0.0
    dist = abs(entry - sl)
    if dist <= 0 or balance <= 0:
        return 0.0
    risk_amount = balance * (max_risk_pct / 100.0)
    return risk_amount / dist


def serialize_pending(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": order.get("id"),
        "market_id": order.get("market_id"),
        "direction": order.get("direction"),
        "order_type": order.get("order_type"),
        "limit_price": order.get("limit_price"),
        "stop_price": order.get("stop_price"),
        "quantity": order.get("quantity"),
        "status": order.get("status", "PENDING"),
    }
