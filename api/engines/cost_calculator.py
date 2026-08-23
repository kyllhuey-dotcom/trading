"""
Correct cost and net RR calculations (v2.7 P0-4).

Fixes the previous unit errors where percentages and absolute distances
were mixed. All calculations use consistent units.

Formula (per unit):
  entry_cost = entry * (fee_pct + slippage_pct) / 100
  round_trip_cost = entry * 2 * (fee_pct + slippage_pct) / 100
  risk_distance = abs(entry - sl)
  cost_to_risk = round_trip_cost / risk_distance

Net RR:
  gross_rr = abs(tp - entry) / risk_distance
  net_rr = (abs(tp - entry) - round_trip_cost) / risk_distance
"""
from typing import Any


def compute_trade_costs(
    entry: float,
    sl: float,
    tp: float,
    fee_pct: float = 0.05,
    slippage_pct: float = 0.05,
    spread: float | None = None,
    funding_pct: float = 0.0,
    conversion_pct: float = 0.0,
    bid: float | None = None,
    ask: float | None = None,
) -> dict[str, Any]:
    """Compute all cost components for a trade.

    Parameters are in percentage terms (e.g. 0.05 = 0.05%).
    spread, if provided, is in absolute price units.
    """
    entry = float(entry)
    sl = float(sl)
    tp = float(tp)

    if entry <= 0:
        return {"valid": False, "reason": "Invalid entry price"}

    risk_distance = abs(entry - sl)
    if risk_distance <= 0:
        return {"valid": False, "reason": "Zero risk distance"}

    # Real spread cost from bid/ask if available
    spread_cost = 0.0
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        spread_cost = abs(ask - bid)
    elif spread is not None and spread > 0:
        spread_cost = float(spread)

    # Fee and slippage in absolute terms per unit
    fee_abs = entry * (fee_pct / 100.0)
    slippage_abs = entry * (slippage_pct / 100.0)
    funding_abs = entry * (funding_pct / 100.0)
    conversion_abs = entry * (conversion_pct / 100.0)

    # Entry cost (one side)
    entry_cost = fee_abs + slippage_abs + (spread_cost / 2.0) + conversion_abs

    # Round-trip cost (entry + exit)
    round_trip_cost = (fee_abs + slippage_abs) * 2.0 + spread_cost + funding_abs + conversion_abs * 2.0

    # Cost-to-risk ratio
    cost_to_risk = round_trip_cost / risk_distance if risk_distance > 0 else float("inf")

    # Gross and net RR
    tp_distance = abs(tp - entry)
    gross_rr = tp_distance / risk_distance if risk_distance > 0 else 0.0
    net_rr = (tp_distance - round_trip_cost) / risk_distance if risk_distance > 0 else 0.0

    return {
        "valid": True,
        "entry_cost": round(entry_cost, 8),
        "round_trip_cost": round(round_trip_cost, 8),
        "risk_distance": round(risk_distance, 8),
        "cost_to_risk": round(cost_to_risk, 4),
        "gross_rr": round(gross_rr, 4),
        "net_rr": round(net_rr, 4),
        "fee_abs": round(fee_abs, 8),
        "slippage_abs": round(slippage_abs, 8),
        "spread_cost": round(spread_cost, 8),
        "funding_abs": round(funding_abs, 8),
        "conversion_abs": round(conversion_abs, 8),
        "tp_distance": round(tp_distance, 8),
    }


def costs_pass_gate(costs: dict[str, Any], min_net_rr: float = 1.5,
                    max_cost_to_risk: float = 0.5) -> dict[str, Any]:
    """Check whether a trade passes the cost gate.

    Returns {allowed, reason} with specific rejection reasons.
    """
    if not costs.get("valid"):
        return {"allowed": False, "reason": costs.get("reason", "Invalid cost calculation")}

    net_rr = costs.get("net_rr", 0.0)
    cost_to_risk = costs.get("cost_to_risk", float("inf"))

    if net_rr < min_net_rr:
        return {
            "allowed": False,
            "reason": f"Net RR {net_rr:.2f} < {min_net_rr} (costs eat the edge)",
        }
    if cost_to_risk > max_cost_to_risk:
        return {
            "allowed": False,
            "reason": f"Cost-to-risk {cost_to_risk:.2f} > {max_cost_to_risk} (fees too high vs risk)",
        }
    return {"allowed": True, "reason": None}


def recompute_after_fill(
    fill_price: float,
    original_sl: float,
    original_tp: float,
    fee_pct: float = 0.05,
    slippage_pct: float = 0.05,
    spread: float | None = None,
    direction: str = "BUY",
) -> dict[str, Any]:
    """Recompute risk and RR after a real fill.

    If the fill degrades the net RR below 1.5, the caller should
    refuse to open the position or close immediately.
    """
    fill_price = float(fill_price)
    sl = float(original_sl)
    tp = float(original_tp)

    # Adjust SL/TP relative to actual fill price
    if direction == "BUY":
        risk_distance = fill_price - sl
        tp - fill_price
    else:
        risk_distance = sl - fill_price
        fill_price - tp

    if risk_distance <= 0:
        return {"valid": False, "reason": "Fill price invalidates SL", "action": "REFUSE"}

    costs = compute_trade_costs(
        entry=fill_price, sl=sl, tp=tp,
        fee_pct=fee_pct, slippage_pct=slippage_pct,
        spread=spread,
    )
    if not costs.get("valid"):
        return {**costs, "action": "REFUSE"}

    if costs["net_rr"] < 1.5:
        return {
            **costs,
            "action": "DEGRADED",
            "reason": f"Fill degraded net RR to {costs['net_rr']:.2f} < 1.5",
        }

    return {**costs, "action": "OK"}
