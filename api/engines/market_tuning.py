"""
Per-market parameter tuning, volatility-regime adaptation & capital feasibility.

Motivation (Lot R — "audit des performances par marché")
--------------------------------------------------------
The capital profiles (`capital_profiles.py`) tune the bot *globally* per capital
bracket, and the profit audit (`scripts/profit_audit.py`) judged strategies.
The user's audit methodology also requires acting **per financial market**:

1. identify the markets that were the most profitable (and the losing ones);
2. know which markets work with which capital level (1 $ → 50 $+);
3. adapt the aggressiveness to market conditions (conservative when volatile,
   more engaged when stable);
4. optimize the trading parameters **for each market**: entry threshold
   (`min_score`), stop-loss distance (`atr_stop_multiplier`) and take-profit
   (`risk_reward`).

This module provides:

- `ASSET_CLASS_TUNING`: baseline tuning per asset class (crypto ≠ forex ≠
  bonds: fees, sessions and volatility regimes differ).
- `build_default_tuning(universe)`: one tuning entry per market of the universe.
- `regime_of(volatility_label)`: VOLATILE / NORMAL / QUIET from the analysis
  engine's volatility label.
- `regime_adjustments(regime)`: conservative adjustments in volatile markets,
  slightly more permissive in quiet/stable ones (bounded, never below the
  configured floor).
- `resolve_market_tuning(market_id, tuning_map)`: effective per-market params.
- `min_capital_for(market_info, leverage_cap)`: estimated minimal capital to
  trade a market for real (margin ≈ min_notional / leverage).
- `markets_feasible_for_capital(balance, universe)`: which markets / asset
  classes actually work at a given capital level.
- `recommend_for_market(market_stats, balance)`: audit-driven verdict + tuned
  parameters for ONE market (raise entry threshold on a losing market, widen
  take-profit when the realized RR is too small, …).
- `build_tuning_from_audit(per_market_stats, balance, universe)`: merge the
  defaults with the audit-driven overrides → ready to paste into the
  `market_tuning` setting (or to push via `signal_engine.set_market_tuning`).

Honesty note
------------
A "99 % win rate" objective is not achievable in real markets (see
`capital_profiles.HEALTH_TARGETS`). Per-market optimization targets the same
realistic health metrics: win rate ≥ 45 %, realized RR ≥ 1.5, expectancy > 0,
zero cost leaks. A market below these targets gets *more selective* (higher
entry threshold) or is quarantined — never martingale'd back.
"""
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------- #
# Baseline tuning per asset class                                              #
# --------------------------------------------------------------------------- #
# Modest, defensible differences: entry threshold (min_score), take-profit
# (risk_reward) and stop distance (atr_stop_multiplier). These are *starting
# points* — the audit-driven overrides refine them per market.
ASSET_CLASS_TUNING: Dict[str, Dict[str, Any]] = {
    "CRYPTO": {
        "min_score": 80, "risk_reward": 2.5, "atr_stop_multiplier": 1.5,
        "note": "24/7 temps réel, volatilité native : RR ambitieux, sélectivité standard.",
    },
    "FOREX": {
        "min_score": 82, "risk_reward": 2.0, "atr_stop_multiplier": 1.8,
        "note": "Spread relatif élevé vs mouvement 1m : stop plus large, RR modéré.",
    },
    "INDICES": {
        "min_score": 82, "risk_reward": 2.0, "atr_stop_multiplier": 1.8,
        "note": "Sessions + news macro : sélectivité un peu plus haute.",
    },
    "COMMODITIES": {
        "min_score": 83, "risk_reward": 2.2, "atr_stop_multiplier": 1.8,
        "note": "Gaps de session (or/pétrole) : stop élargi.",
    },
    "STOCKS": {
        "min_score": 85, "risk_reward": 2.0, "atr_stop_multiplier": 1.8,
        "note": "Données différées (garde anti-scalping) : sélectivité haute.",
    },
    "FUTURES": {
        "min_score": 85, "risk_reward": 2.2, "atr_stop_multiplier": 2.0,
        "note": "Contrats + expiration : stop large, RR modéré.",
    },
    "BONDS": {
        "min_score": 85, "risk_reward": 1.8, "atr_stop_multiplier": 2.0,
        "note": "Mouvements lents : RR réaliste, stop large.",
    },
    "ETFS": {
        "min_score": 85, "risk_reward": 1.8, "atr_stop_multiplier": 2.0,
        "note": "Mouvements lents : RR réaliste, stop large.",
    },
}

DEFAULT_TUNING: Dict[str, Any] = ASSET_CLASS_TUNING["CRYPTO"]

# Guard rails for every per-market value (v2.7: floor raised to 80).
BOUNDS = {
    "min_score": (80, 99),
    "risk_reward": (0.5, 10.0),
    "atr_stop_multiplier": (0.1, 10.0),
    "max_cost_ratio": (0.0, 2.0),
}

# A market needs a minimum number of closed trades before its stats mean
# anything (statistical honesty — no verdict on 2 trades).
# v2.7: raised from 10 to 30 for stronger statistical significance.
MIN_TRADES_FOR_VERDICT = 30


def _clamp(value: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# Regime adaptation (volatile -> conservative, stable -> slightly aggressive)  #
# --------------------------------------------------------------------------- #
def regime_of(volatility_label: Optional[str]) -> str:
    """Map the analysis engine's volatility label to a trading regime.

    `volatility` is "HIGH" / "MEDIUM" / "LOW" (analysis_engine.identify_structure).
    Unknown/missing → NORMAL (no adjustment — fail-neutral).
    """
    v = str(volatility_label or "").upper()
    if v == "HIGH":
        return "VOLATILE"
    if v == "LOW":
        return "QUIET"
    return "NORMAL"


def regime_adjustments(regime: str) -> Dict[str, Any]:
    """Parameter adjustments per market regime.

    - VOLATILE (news shock, spike) → **conservative**: entry threshold +5,
      stop widened ×1.25 (the wider stop automatically shrinks the position
      size for the same risk % — that is the risk reduction).
    - QUIET / stable → slightly **more engaged**: threshold −3 (floored at the
      configured minimum), stop ×0.90. Never lowers below the global floor.
    - NORMAL → no change.
    """
    if regime == "VOLATILE":
        return {"min_score_delta": +5, "atr_multiplier_factor": 1.25, "style": "conservative"}
    if regime == "QUIET":
        return {"min_score_delta": -3, "atr_multiplier_factor": 0.90, "style": "engaged"}
    return {"min_score_delta": 0, "atr_multiplier_factor": 1.0, "style": "neutral"}


# --------------------------------------------------------------------------- #
# Per-market tuning resolution                                                 #
# --------------------------------------------------------------------------- #
def build_default_tuning(universe: Any) -> Dict[str, Dict[str, Any]]:
    """One tuning entry per market of the universe (from its asset class)."""
    tuning: Dict[str, Dict[str, Any]] = {}
    try:
        ids = universe.get_all_ids()
    except Exception:
        return tuning
    for mid in ids:
        info = universe.get_info(mid) or {}
        base = dict(ASSET_CLASS_TUNING.get(info.get("asset_class", "CRYPTO"), DEFAULT_TUNING))
        base.pop("note", None)
        tuning[mid] = base
    return tuning


def resolve_market_tuning(market_id: Optional[str],
                          tuning_map: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    """Effective tuning for a market (empty dict when nothing is configured).

    Invalid values (non-numeric / None) are dropped rather than clamped.
    """
    if not market_id or not tuning_map:
        return {}
    entry = tuning_map.get(market_id) or {}
    out: Dict[str, Any] = {}
    for key, (lo, hi) in BOUNDS.items():
        if key in entry:
            try:
                out[key] = max(lo, min(hi, float(entry[key])))
            except (TypeError, ValueError):
                continue
    return out


# --------------------------------------------------------------------------- #
# Capital feasibility ("which markets work at which capital level")            #
# --------------------------------------------------------------------------- #
def min_capital_for(market_info: Dict[str, Any],
                    leverage_cap: float = 10.0,
                    safety_margin: float = 1.2) -> float:
    """Estimated minimal capital to trade a market for real.

    Approximation: required margin ≈ min_notional / effective_leverage, with a
    safety margin so the account is not 100 % committed on one position.
    In DEMO (paper) everything is feasible — this estimate targets REAL mode
    where exchange/broker minimums actually bite.
    """
    try:
        min_notional = float(market_info.get("min_order", 0) or 0)
        lev_max = float(market_info.get("leverage_max", 1) or 1)
    except (TypeError, ValueError):
        return float("inf")
    effective_leverage = max(1.0, min(lev_max, max(1.0, float(leverage_cap))))
    if min_notional <= 0:
        return 0.0
    return (min_notional / effective_leverage) * safety_margin


def markets_feasible_for_capital(balance: float, universe: Any,
                                 leverage_cap: float = 20.0) -> Dict[str, Any]:
    """Classify the universe by feasibility at a given balance.

    Returns per asset class: number of feasible markets, the cheapest markets
    and whether the class is realtime (auto-executable) or delayed (Yahoo).
    """
    summary: Dict[str, Dict[str, Any]] = {}
    try:
        ids = universe.get_all_ids()
    except Exception:
        return {"balance": balance, "asset_classes": {}}

    for mid in ids:
        info = universe.get_info(mid) or {}
        klass = info.get("asset_class", "UNKNOWN")
        need = min_capital_for(info, leverage_cap=leverage_cap)
        entry = summary.setdefault(klass, {
            "asset_class": klass,
            "markets_total": 0, "markets_feasible": 0, "cheapest": [],
            "min_capital_estimate": float("inf"),
        })
        entry["markets_total"] += 1
        entry["min_capital_estimate"] = min(entry["min_capital_estimate"], need)
        feasible = balance >= need
        if feasible:
            entry["markets_feasible"] += 1
        entry["cheapest"].append({"market_id": mid, "min_capital_estimate": round(need, 2),
                                  "realtime": "yahoo" not in "|".join((info.get("providers") or {}).keys()),
                                  "feasible": feasible})
        entry["cheapest"].sort(key=lambda x: x["min_capital_estimate"])

    for entry in summary.values():
        entry["cheapest"] = entry["cheapest"][:8]
        if entry["min_capital_estimate"] == float("inf"):
            entry["min_capital_estimate"] = None
        entry["class_feasible"] = entry["markets_feasible"] > 0
    return {"balance": balance, "asset_classes": summary}


# --------------------------------------------------------------------------- #
# Audit-driven per-market recommendations                                      #
# --------------------------------------------------------------------------- #
def recommend_for_market(market_id: str, stats: Optional[Dict[str, Any]],
                         balance: float = 0.0) -> Dict[str, Any]:
    """Verdict + tuned parameters for ONE market from its audit stats.

    `stats` is one entry of the audit's `by_market` map (trades, wins,
    win_rate, net_pnl, avg_win, avg_loss, realized_rr, cost_leaks…).
    The recommended `min_score` / `risk_reward` / `atr_stop_multiplier` are
    per-market overrides ready for `signal_engine.set_market_tuning`.
    """
    from api.engines.capital_profiles import profile_overrides  # local: avoid cycle

    base = profile_overrides(balance) if balance else {
        "min_signal_score": 80, "risk_reward_ratio": 2.0, "atr_stop_multiplier": 1.5,
    }
    rec: Dict[str, Any] = {
        "market_id": market_id,
        "trades": 0,
        "verdict": "INSUFFICIENT_DATA",
        "action": "OBSERVE",
        "recommend": (f"Pas encore {MIN_TRADES_FOR_VERDICT} trades fermés sur ce marché : "
                      f"continuer à observer, ne pas tuner sur du bruit."),
        "params": {
            "min_score": int(base.get("min_signal_score", 80)),
            "risk_reward": float(base.get("risk_reward_ratio", 2.0)),
            "atr_stop_multiplier": float(base.get("atr_stop_multiplier", 1.5)),
        },
    }
    if not stats:
        return rec

    trades = int(stats.get("trades", 0) or 0)
    rec["trades"] = trades
    if trades < MIN_TRADES_FOR_VERDICT:
        return rec

    win_rate = float(stats.get("win_rate", 0) or 0)
    pnl = float(stats.get("net_pnl", 0) or 0)
    rr = stats.get("realized_rr")
    rr = float(rr) if rr is not None else None
    cost_leaks = int(stats.get("cost_leaks", 0) or 0)
    expectancy = float(stats.get("expectancy_per_trade", pnl / trades) or 0.0)

    params = rec["params"]

    if pnl < 0 or win_rate < 45:
        rec["verdict"] = "LOSING"
        rec["action"] = "QUARANTINE_OR_RAISE_SELECTIVITY"
        params["min_score"] = int(_clamp(int(base.get("min_signal_score", 80)) + 10, *BOUNDS["min_score"]))
        rec["recommend"] = (f"Marché perdant (win rate {win_rate:.1f}%, PnL {pnl:+.2f}) → "
                            f"relever le seuil d'entrée à {params['min_score']} "
                            f"(uniquement les setups majeurs) ou suspendre ce marché.")
    elif rr is not None and rr < 1.5:
        rec["verdict"] = "TP_TOO_TIGHT"
        rec["action"] = "WIDEN_TAKE_PROFIT"
        params["risk_reward"] = round(_clamp(float(base.get("risk_reward_ratio", 2.0)) + 0.5, *BOUNDS["risk_reward"]), 2)
        params["atr_stop_multiplier"] = round(_clamp(float(base.get("atr_stop_multiplier", 1.5)) + 0.5, *BOUNDS["atr_stop_multiplier"]), 2)
        rec["recommend"] = (f"RR réalisé {rr:.2f} < 1.5 → les sorties coupent les gains : "
                            f"TP porté à {params['risk_reward']}R et stop ATR élargi à "
                            f"{params['atr_stop_multiplier']} sur ce marché.")
    elif cost_leaks > 0:
        rec["verdict"] = "COST_LEAK"
        rec["action"] = "TIGHTEN_COST_FILTER"
        params["max_cost_ratio"] = 0.4
        rec["recommend"] = (f"{cost_leaks} trade(s) dont les frais dépassaient 50 % du risque → "
                            f"abaisser max_cost_ratio à 0.4 pour ce marché (trades plus longs / stop plus large).")
    elif win_rate >= 45 and expectancy > 0 and pnl > 0:
        rec["verdict"] = "PROFITABLE"
        rec["action"] = "KEEP_AND_SCALE"
        params["min_score"] = int(_clamp(int(base.get("min_signal_score", 80)) - 3, *BOUNDS["min_score"]))
        rec["recommend"] = (f"Marché sain (win rate {win_rate:.1f}%, espérance {expectancy:+.2f}) → "
                            f"maintenir, seuil d'entrée desserré à {params['min_score']} pour "
                            f"capitaliser sur l'edge, et scaler progressivement.")
    else:
        rec["verdict"] = "BREAKEVEN"
        rec["action"] = "REVIEW"
        rec["recommend"] = "Pas de fuite évidente mais edge faible : maintenir et ré-auditer après 10 trades de plus."
    return rec


def build_tuning_from_audit(per_market_stats: Optional[Dict[str, Dict[str, Any]]],
                            balance: float,
                            universe: Any = None) -> Dict[str, Dict[str, Any]]:
    """Full `market_tuning` map: asset-class defaults + audit-driven overrides.

    Only *meaningful* deviations are stored (a market that matches its class
    baseline adds no entry) so the setting stays readable.
    """
    tuning = build_default_tuning(universe) if universe is not None else {}
    if not per_market_stats:
        return tuning
    for market_id, stats in per_market_stats.items():
        rec = recommend_for_market(market_id, stats, balance)
        if rec.get("action") in ("OBSERVE", "KEEP_AND_SCALE") and rec["verdict"] == "INSUFFICIENT_DATA":
            continue
        base = tuning.get(market_id) or {"min_score": 80, "risk_reward": 2.0,
                                         "atr_stop_multiplier": 1.5}
        override = {k: v for k, v in rec.get("params", {}).items()
                    if k in BOUNDS and v is not None}
        merged = dict(base)
        merged.update(override)
        tuning[market_id] = merged
    return tuning
