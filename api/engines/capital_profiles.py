"""
Capital-aware profiles & audit-driven optimization.

Motivation
----------
A $1 account and a $10,000 account cannot (and must not) use the same
parameters:

- **Micro accounts** (up to ~$10) have almost no buffer. A single loss is a
  large fraction of equity, so they need *higher selectivity* (higher minimum
  score), *fewer concurrent positions*, *lower leverage* and *wider stops in
  ATR* so they are not stopped out by fee noise.
- **Standard accounts** can afford more diversification (more positions,
  higher RR) because a single loss is a small fraction of equity.

This module defines the brackets and provides:

- `BRACKETS`: the ordered list of capital brackets.
- `resolve_bracket(balance)`: pick the bracket for a given account balance.
- `profile_overrides(balance)`: the concrete parameter overrides for that
  bracket (ready to be pushed into RiskEngine / SignalEngine).
- `recommend_from_audit(audit_stats, balance)`: turn a `profit_audit.py`
  report into actionable parameter recommendations per strategy.

The engine wires this through `capital_profile_mode`:
  - `manual` (default): the user's explicit settings win. The bracket is only
    reported, not enforced.
  - `auto`: the bracket overrides risk pct, max leverage, max positions,
    min score, risk-reward, ATR stop multiplier, min trade notional and the
    cost filter, so the bot adapts itself to the size of the account.

Honesty note
------------
A "99% win rate" target (as requested in the agent prompt) is **not
achievable** in real markets. The realistic profit target is a *positive
expectancy* (maximize expectancy ≥ +0.5R, realized RR ≥ 1.5, and a profit
factor > 1.3) while cutting structural leaks (cost leaks, below-threshold
signals, wide trailing). The optimizer below encodes those realistic targets.
"""
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Brackets                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class CapitalBracket:
    """A bracket of account balance and the parameters that fit it."""
    name: str
    min_balance: float
    max_balance: float              # exclusive upper bound (inf -> open-ended)
    risk_pct: float
    risk_reward: float
    min_score: int
    max_positions: int
    max_leverage: float
    atr_stop_multiplier: float
    min_trade_notional: float
    max_cost_ratio: float
    note: str = ""


# Balanced, conservative defaults tuned for each capital tier. These are
# *starting points* meant to be refined by audit-driven optimization.
BRACKETS: List[CapitalBracket] = [
    CapitalBracket(
        name="MICRO",
        min_balance=0.0,
        max_balance=10.0,
        risk_pct=1.0,
        risk_reward=2.5,
        min_score=85,
        max_positions=3,
        max_leverage=10,
        atr_stop_multiplier=2.0,
        min_trade_notional=1.0,
        max_cost_ratio=0.5,
        note="Capital très petit : sélectivité très haute, stop large, "
             "très peu de positions, levier réduit. Aucun martingale.",
    ),
    CapitalBracket(
        name="RETAIL",
        min_balance=10.0,
        max_balance=50.0,
        risk_pct=1.5,
        risk_reward=2.5,
        min_score=80,
        max_positions=5,
        max_leverage=15,
        atr_stop_multiplier=1.5,
        min_trade_notional=5.0,
        max_cost_ratio=0.5,
        note="Capital intermédiaire : un peu plus de positions, score 80, "
             "levier modéré.",
    ),
    CapitalBracket(
        name="STANDARD",
        min_balance=50.0,
        max_balance=float("inf"),
        risk_pct=2.0,
        risk_reward=3.0,
        min_score=80,
        max_positions=10,
        max_leverage=20,
        atr_stop_multiplier=1.5,
        min_trade_notional=10.0,
        max_cost_ratio=0.5,
        note="Capital standard et au-delà : diversification maximale, "
             "RR plus ambitieux, score plancher 80 inviolable.",
    ),
]

# Realistic profit/health targets (NOT an achievable 99% win rate).
HEALTH_TARGETS: Dict[str, float] = {
    "min_win_rate_pct": 45.0,
    "min_realized_rr": 1.5,
    "min_expectancy_r": 0.5,
    "min_profit_factor": 1.3,
}

# Keys the bracket overrides, mapped to the target setter/attribute.
OVERRIDABLE_PARAMS: List[str] = [
    "max_risk_pct", "max_leverage", "max_open_positions",
    "min_signal_score", "risk_reward_ratio", "atr_stop_multiplier",
    "min_trade_notional", "max_cost_ratio",
]


def resolve_bracket(balance: float) -> CapitalBracket:
    """Return the bracket for a given account balance (equity)."""
    if balance is None or balance < 0:
        balance = 0.0
    for b in BRACKETS:
        if b.min_balance <= balance < b.max_balance:
            return b
    return BRACKETS[-1]


def profile_overrides(balance: float) -> Dict[str, Any]:
    """The parameter overrides (settings names -> value) for a bracket."""
    b = resolve_bracket(balance)
    return {
        "bracket": b.name,
        "risk_pct": b.risk_pct,
        "max_leverage": b.max_leverage,
        "max_open_positions": b.max_positions,
        "min_signal_score": b.min_score,
        "risk_reward_ratio": b.risk_reward,
        "atr_stop_multiplier": b.atr_stop_multiplier,
        "min_trade_notional": b.min_trade_notional,
        "max_cost_ratio": b.max_cost_ratio,
        "note": b.note,
    }


def bracket_summary() -> List[Dict[str, Any]]:
    """Human-readable list of the brackets."""
    return [asdict(b) for b in BRACKETS]


# --------------------------------------------------------------------------- #
# Audit-driven optimization                                                     #
# --------------------------------------------------------------------------- #
def _expectancy_pct(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Expectancy in % of a 1R risk, from realized win rate and payoff."""
    if avg_win <= 0 and avg_loss <= 0:
        return 0.0
    return (win_rate / 100.0) * avg_win - (1 - win_rate / 100.0) * abs(avg_loss)


def recommend_from_audit(audit_stats: Optional[Dict[str, Any]],
                         balance: float) -> Dict[str, Any]:
    """
    Turn a profit_audit report into actionable recommendations.

    `audit_stats` is the structure returned by `scripts/profit_audit.py`
    (`{"modes": {mode: {"by_strategy": {strategy: {...}}}}, ...}`).
    With no data (`audit_stats` None / no tiers), returns sensible defaults
    based on the account bracket.
    """
    base = profile_overrides(balance)
    recommendations: Dict[str, Any] = {
        "bracket": base["bracket"],
        "account_balance": balance,
        "targets": dict(HEALTH_TARGETS),
        "per_strategy": {},
        "recommended_settings": {
            "max_risk_pct": base["risk_pct"],
            "max_leverage": base["max_leverage"],
            "max_open_positions": base["max_open_positions"],
            "min_signal_score": base["min_signal_score"],
            "risk_reward_ratio": base["risk_reward_ratio"],
            "atr_stop_multiplier": base["atr_stop_multiplier"],
            "min_trade_notional": base["min_trade_notional"],
            "max_cost_ratio": base["max_cost_ratio"],
        },
        "health_verdict": "N/A (no trade data — audit is code/config based)",
    }

    if not audit_stats:
        return recommendations

    modes = audit_stats.get("modes", {})
    if not modes:
        return recommendations

    # Aggregate per strategy across modes (closing the strategy anyway if it
    # is structurally negative in every mode).
    agg: Dict[str, Dict[str, float]] = {}
    for m in modes.values():
        for strat, s in (m.get("by_strategy") or {}).items():
            a = agg.setdefault(strat, {"trades": 0, "wins": 0, "pnl": 0.0,
                                       "avg_win": 0.0, "avg_loss": 0.0,
                                       "cost_leaks": 0, "all": 0.0})
            n = float(s.get("trades", 0) or 0)
            a["trades"] += n
            a["all"] += n
            a["wins"] += float(s.get("wins", 0) or 0)
            a["pnl"] += float(s.get("net_pnl", 0) or 0)
            a["avg_win"] = max(a["avg_win"], float(s.get("avg_win", 0) or 0))
            a["avg_loss"] = max(a["avg_loss"], float(s.get("avg_loss", 0) or 0))
            a["cost_leaks"] += float(s.get("cost_leaks", 0) or 0)

    worst_win_rate = 100.0
    best = {"name": None, "expectancy": -1e9}
    for strat, a in agg.items():
        n = a["trades"]
        if n <= 0:
            continue
        win_rate = a["wins"] / n * 100.0
        expectancy = _expectancy_pct(win_rate, a["avg_win"], a["avg_loss"])
        realized_rr = (a["avg_win"] / a["avg_loss"]) if a["avg_loss"] > 0 else None
        edge = "PROFITABLE" if (a["pnl"] > 0 and win_rate >= HEALTH_TARGETS["min_win_rate_pct"]) \
            else ("LOSING" if (a["pnl"] < 0 or win_rate < HEALTH_TARGETS["min_win_rate_pct"]) else "BREAKEVEN")
        worst_win_rate = min(worst_win_rate, win_rate)

        rec: Dict[str, Any] = {
            "trades": n,
            "win_rate": round(win_rate, 1),
            "net_pnl": round(a["pnl"], 2),
            "realized_rr": round(realized_rr, 2) if realized_rr is not None else None,
            "expectancy": round(expectancy, 3),
            "cost_leaks": a["cost_leaks"],
            "verdict": edge,
        }
        # Optimization rules -------------------------------------------------
        if edge == "LOSING":
            rec["action"] = "DISABLE_OR_RAISE_SELECTIVITY"
            rec["recommend"] = (f"Win rate {win_rate:.1f}% < 45% → soit désactiver "
                                f"la stratégie, soit relever le score minimum à "
                                f"{int(min(95, base['min_signal_score'] + 10))} et renforcer le "
                                f"filtre de coûts (max_cost_ratio = "
                                f"{max(0.1, base['max_cost_ratio'] - 0.1)}).")
        elif realized_rr is not None and realized_rr < HEALTH_TARGETS["min_realized_rr"]:
            rec["action"] = "WIDEN_TAKE_PROFIT"
            rec["recommend"] = (f"RR réalisé {realized_rr:.2f} < 1.5 → les sorties "
                                f"coupent les gains. Repasser le TP sur "
                                f"{min(10.0, base['risk_reward_ratio'] + 0.5)}R et "
                                f"relâcher le trailing stop (ATR stop = "
                                f"{min(10.0, base['atr_stop_multiplier'] + 0.5)}).")
        elif a["cost_leaks"] > 0:
            rec["action"] = "TIGHTEN_COST_FILTER"
            rec["recommend"] = (f"{a['cost_leaks']} trade(s) dont les frais "
                                f"dépassaient 50 % du risque. Activer le filtre "
                                f"de coûts sur TOUTES les stratégies et abaisser "
                                f"max_cost_ratio à {max(0.05, base['max_cost_ratio'] - 0.1)}.")
        elif win_rate >= HEALTH_TARGETS["min_win_rate_pct"] and expectancy > 0:
            rec["action"] = "KEEP"
            rec["recommend"] = (f"Stratégie saine : win rate {win_rate:.1f}%, "
                                f"espérance {expectancy:+.3f}R. Maintenir et "
                                f"scaler progressivement.")
        else:
            rec["action"] = "REVIEW"
            rec["recommend"] = "Réviser : pas de fuite évidente mais edge faible."

        recommendations["per_strategy"][strat] = rec
        if expectancy > best["expectancy"]:
            best = {"name": strat, "expectancy": expectancy}

    # Overall health verdict + best strategy.
    recommendations["best_strategy"] = best
    if worst_win_rate >= HEALTH_TARGETS["min_win_rate_pct"]:
        recommendations["health_verdict"] = "HEALTHY"
    elif worst_win_rate >= 35:
        recommendations["health_verdict"] = "NEEDS_TAILORING"
    else:
        recommendations["health_verdict"] = "UNHEALTHY"
    return recommendations
