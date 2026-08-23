"""
Opportunity Ranker (v2.7 P0-2).

Pure, tested module that ranks candidates and selects the single best
opportunity for execution. Replaces the previous "fill all slots" behavior.

The ranker:
1. Excludes any candidate failing a gate (score < 80, news blocked, etc.)
2. Computes quality metrics for survivors
3. Ranks by: gates > net edge > score > freshness > spread > reliability
4. Returns primary_opportunity + secondary_opportunities
"""
import hashlib
import time
from typing import Any

from .constants import (
    AUTO_EXECUTION_SCORE_FLOOR,
    DEFAULT_MAX_NEW_POSITIONS_PER_SCAN,
    SHRINKAGE_PRIOR_TRADES,
    SHRINKAGE_PRIOR_WIN_RATE,
)
from .cost_calculator import compute_trade_costs as _compute_costs


def _bayesian_shrink(observed: float, n: int,
                     prior: float = SHRINKAGE_PRIOR_WIN_RATE,
                     prior_weight: int = SHRINKAGE_PRIOR_TRADES) -> float:
    """Bayesian shrinkage toward a neutral prior when sample is small."""
    if n <= 0:
        return prior
    return (observed * n + prior * prior_weight) / (n + prior_weight)


def _stable_opportunity_id(symbol: str, strategy: str, direction: str,
                           cycle_ts: float) -> str:
    """Deterministic opportunity ID stable within a scan cycle."""
    raw = f"{symbol}|{strategy}|{direction}|{int(cycle_ts)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_candidate_metrics(
    candidate: dict[str, Any],
    fee_pct: float = 0.05,
    slippage_pct: float = 0.05,
    strategy_reliability: dict[str, Any] | None = None,
    market_reliability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute quality metrics for a single candidate."""
    sig = candidate.get("signal_data") or {}
    entry = float(sig.get("entry", 0) or 0)
    sl = float(sig.get("sl", 0) or 0)
    tp = float(sig.get("tp", 0) or 0)
    score = int(candidate.get("score", 0) or 0)

    risk_distance = abs(entry - sl) if entry > 0 and sl > 0 else 0.0
    tp_distance = abs(tp - entry) if entry > 0 and tp > 0 else 0.0

    # Cost calculations
    fee_abs = entry * (fee_pct / 100.0) if entry > 0 else 0.0
    slip_abs = entry * (slippage_pct / 100.0) if entry > 0 else 0.0
    spread_abs = float(candidate.get("spread", 0) or 0)
    round_trip_cost = (fee_abs + slip_abs) * 2.0 + spread_abs

    gross_rr = tp_distance / risk_distance if risk_distance > 0 else 0.0
    net_rr = (tp_distance - round_trip_cost) / risk_distance if risk_distance > 0 else 0.0
    cost_to_risk = round_trip_cost / risk_distance if risk_distance > 0 else float("inf")

    # Data freshness
    data_age_ms = candidate.get("data_age_ms") or 0

    # Spread percentage
    spread_pct = (spread_abs / entry * 100) if entry > 0 else 0.0

    # Liquidity score (volume-based)
    volume = float(candidate.get("volume", 0) or 0)
    liquidity_score = min(1.0, volume / 1_000_000) if volume > 0 else 0.0

    # Strategy and market reliability (with shrinkage)
    strat_name = sig.get("strategy", candidate.get("strategy", "structure"))
    strat_data = (strategy_reliability or {}).get(strat_name, {})
    strat_wr = _bayesian_shrink(
        float(strat_data.get("win_rate", 0.45) or 0.45),
        int(strat_data.get("trades", 0) or 0),
    )
    mkt_id = candidate.get("symbol", "")
    mkt_data = (market_reliability or {}).get(mkt_id, {})
    mkt_wr = _bayesian_shrink(
        float(mkt_data.get("win_rate", 0.45) or 0.45),
        int(mkt_data.get("trades", 0) or 0),
    )

    regime = sig.get("regime", "NORMAL")

    return {
        "quality_score": score,
        "configured_threshold": AUTO_EXECUTION_SCORE_FLOOR,
        "estimated_gross_rr": round(gross_rr, 4),
        "estimated_net_rr": round(net_rr, 4),
        "estimated_round_trip_cost": round(round_trip_cost, 8),
        "data_age_ms": int(data_age_ms),
        "spread_pct": round(spread_pct, 4),
        "liquidity_score": round(liquidity_score, 4),
        "regime": regime,
        "strategy_reliability": round(strat_wr, 4),
        "market_reliability": round(mkt_wr, 4),
        "cost_to_risk": round(cost_to_risk, 4),
    }


def _passes_all_gates(
    candidate: dict[str, Any],
    active_symbols: set[str],
    max_spread_pct: float = 0.5,
    min_net_rr: float = 1.5,
    max_cost_to_risk: float = 0.5,
    quarantined: set[str] | None = None,
) -> dict[str, Any]:
    """Check all gates for a candidate. Returns {passes, reasons}.
    
    P0: uses compute_trade_costs from cost_calculator instead of the naive
    0.001*2 formula. Net RR threshold stays 1.5, but the calculation is now
    accurate (fee_pct+slippage_pct as percentages, real spread).
    """
    reasons: list[str] = []
    quarantined = quarantined or set()

    # Gate 1: Score floor
    score = int(candidate.get("score", 0) or 0)
    if score < AUTO_EXECUTION_SCORE_FLOOR:
        reasons.append(f"SCORE_BELOW_FLOOR ({score} < {AUTO_EXECUTION_SCORE_FLOOR})")

    # Gate 2: Signal detected
    sig = candidate.get("signal_data") or {}
    if sig.get("status") != "SIGNAL_DETECTED":
        reasons.append("NO_SIGNAL_DETECTED")

    # Gate 3: News/session
    diagnosis = candidate.get("diagnosis") or {}
    checks = diagnosis.get("checks") or {}
    if checks.get("NEWS_CLEAR") == "FAIL":
        reasons.append("NEWS_BLOCKED")
    if checks.get("SESSION_ALLOWED") == "FAIL":
        reasons.append("SESSION_BLOCKED")
    if checks.get("DAY_ALLOWED") == "FAIL":
        reasons.append("DAY_BLOCKED")

    # Gate 4: Data freshness
    if candidate.get("status") == "DATA_UNAVAILABLE":
        reasons.append("DATA_UNAVAILABLE")
    data_age_ms = candidate.get("data_age_ms") or 0
    if data_age_ms > 60_000:  # 60s max for live execution
        reasons.append(f"DATA_STALE ({data_age_ms}ms)")

    # Gate 5: Spread
    entry = float(sig.get("entry", 0) or 0)
    spread_abs = float(candidate.get("spread", 0) or 0)
    spread_pct = (spread_abs / entry * 100) if entry > 0 else 0.0
    if spread_pct > max_spread_pct:
        reasons.append(f"SPREAD_TOO_HIGH ({spread_pct:.2f}%)")

    # Gate 6: Liquidity
    if checks.get("LIQUIDITY_VALID") == "FAIL":
        reasons.append("INSUFFICIENT_LIQUIDITY")

    # Gate 7: Net RR using compute_trade_costs (P0: not 0.001*2 naive formula)
    sl = float(sig.get("sl", 0) or 0)
    tp = float(sig.get("tp", 0) or 0)
    risk_distance = abs(entry - sl) if entry > 0 and sl > 0 else 0.0
    if risk_distance > 0:
        costs = _compute_costs(
            entry=entry, sl=sl, tp=tp,
            fee_pct=0.05, slippage_pct=0.05,
            spread=spread_abs,
        )
        if costs.get("valid"):
            net_rr = costs["net_rr"]
            cost_to_risk = costs["cost_to_risk"]
            if net_rr < min_net_rr:
                reasons.append(f"NET_RR_TOO_LOW ({net_rr:.2f} < {min_net_rr})")
            if cost_to_risk > max_cost_to_risk:
                reasons.append(f"COST_GATE_BLOCKED (cost_to_risk {cost_to_risk:.2f} > {max_cost_to_risk})")
        else:
            reasons.append("COST_CALCULATION_FAILED")
    else:
        reasons.append("ZERO_RISK_DISTANCE")

    # Gate 8: Quarantine
    symbol = candidate.get("symbol", "")
    strategy = sig.get("strategy", "structure")
    qkey = f"{symbol}:{strategy}"
    if qkey in quarantined:
        reasons.append("QUARANTINED")

    # Gate 9: Position already open
    if symbol in active_symbols:
        reasons.append("POSITION_ALREADY_OPEN")

    # Gate 10: Market open
    if checks.get("MARKET_OPEN") == "FAIL":
        reasons.append("MARKET_CLOSED")

    # Gate 11: Tradable flag
    if not candidate.get("tradable"):
        reasons.append("NOT_TRADABLE")

    return {"passes": len(reasons) == 0, "reasons": reasons}


def rank_opportunities(
    results: list[dict[str, Any]],
    active_symbols: set[str] | None = None,
    max_new_positions: int = DEFAULT_MAX_NEW_POSITIONS_PER_SCAN,
    fee_pct: float = 0.05,
    slippage_pct: float = 0.05,
    max_spread_pct: float = 0.5,
    strategy_reliability: dict[str, Any] | None = None,
    market_reliability: dict[str, Any] | None = None,
    quarantined: set[str] | None = None,
    cycle_ts: float | None = None,
) -> dict[str, Any]:
    """Rank all scan results into primary + secondary opportunities.

    Returns:
        {
            primary_opportunity: {...} or None,
            secondary_opportunities: [...],
            all_candidates: [...],
            cycle_ts: float,
            max_new_positions_per_scan: int,
        }
    """
    active_symbols = active_symbols or set()
    cycle_ts = cycle_ts or time.time()
    max_new_positions = max(1, min(3, int(max_new_positions)))

    # Phase 1: Filter through all gates
    passing: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for candidate in (results or []):
        gate_result = _passes_all_gates(
            candidate, active_symbols,
            max_spread_pct=max_spread_pct,
            quarantined=quarantined,
        )
        if not gate_result["passes"]:
            excluded.append({
                "symbol": candidate.get("symbol"),
                "score": candidate.get("score"),
                "gate_reasons": gate_result["reasons"],
            })
            continue

        # Compute metrics for passing candidates
        metrics = compute_candidate_metrics(
            candidate, fee_pct=fee_pct, slippage_pct=slippage_pct,
            strategy_reliability=strategy_reliability,
            market_reliability=market_reliability,
        )

        # Compute composite rank score
        # Priority: net edge > score > freshness > spread > reliability
        net_rr = metrics["estimated_net_rr"]
        score = metrics["quality_score"]
        age_penalty = min(1.0, metrics["data_age_ms"] / 30_000)  # penalize stale
        spread_penalty = min(1.0, metrics["spread_pct"] / max_spread_pct)
        reliability = (metrics["strategy_reliability"] + metrics["market_reliability"]) / 2.0

        rank_score = (
            net_rr * 40.0 +            # 40% weight on net edge
            (score / 100.0) * 25.0 +   # 25% weight on signal score
            (1.0 - age_penalty) * 15.0 + # 15% weight on freshness
            (1.0 - spread_penalty) * 10.0 + # 10% weight on spread
            reliability * 10.0          # 10% weight on reliability
        )

        sig = candidate.get("signal_data") or {}
        opp_id = _stable_opportunity_id(
            candidate.get("symbol", ""),
            sig.get("strategy", "structure"),
            sig.get("direction", ""),
            cycle_ts,
        )

        rank_reasons = []
        if net_rr >= 2.0:
            rank_reasons.append("HIGH_NET_RR")
        if score >= 90:
            rank_reasons.append("HIGH_SCORE")
        if metrics["data_age_ms"] < 5000:
            rank_reasons.append("FRESH_DATA")
        if metrics["spread_pct"] < 0.1:
            rank_reasons.append("TIGHT_SPREAD")
        if reliability > 0.5:
            rank_reasons.append("RELIABLE_HISTORY")

        passing.append({
            **metrics,
            "symbol": candidate.get("symbol"),
            "display_symbol": candidate.get("display_symbol"),
            "direction": sig.get("direction"),
            "strategy": sig.get("strategy", "structure"),
            "entry": sig.get("entry"),
            "sl": sig.get("sl"),
            "tp": sig.get("tp"),
            "score": score,
            "rank_score": round(rank_score, 4),
            "rank_reasons": rank_reasons,
            "opportunity_id": opp_id,
            "signal_data": sig,
            "diagnosis": candidate.get("diagnosis"),
            "realtime_source": candidate.get("realtime_source"),
            "asset_class": candidate.get("asset_class"),
            # P0-3 (2026-08-23): execution-critical flags are copied verbatim
            # from the raw scan row so downstream consumers (execution loop,
            # /api/opportunities) never need to re-join with raw results.
            "tradable": bool(candidate.get("tradable")),
            "status": candidate.get("status"),
            "active_source": candidate.get("active_source"),
            "underlying": candidate.get("underlying"),
            "market_status": candidate.get("market_status"),
            "block_reason": candidate.get("block_reason"),
            "expires_at": cycle_ts + 30.0,  # 30s TTL
            "signal_age_ms": metrics["data_age_ms"],
        })

    # Phase 2: Sort by rank_score descending
    passing.sort(key=lambda x: x["rank_score"], reverse=True)

    primary = passing[0] if passing else None
    secondary = passing[1:] if len(passing) > 1 else []

    return {
        "primary_opportunity": primary,
        "secondary_opportunities": secondary,
        "excluded": excluded[:10],  # top 10 exclusions for visibility
        "all_candidates": passing,
        "cycle_ts": cycle_ts,
        "max_new_positions_per_scan": max_new_positions,
        "total_evaluated": len(results or []),
        "total_passing": len(passing),
        "total_excluded": len(excluded),
    }
