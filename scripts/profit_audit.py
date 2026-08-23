#!/usr/bin/env python3
"""
Profit audit — Quantum Trade Pro (LOT P + LOT R).

Reads a bot database (default data/quantum_trade.db, or any path — e.g. your
Railway volume export) and reports, per mode and per strategy:
- number of closed trades, win rate, net PnL, avg win / avg loss;
- expectancy per trade and realized reward:risk;
- trades whose round-trip costs were too high vs the risk taken (leaks);
- warnings and a profitability verdict per strategy.

LOT R adds the **per-market** audit requested by the methodology:
- stats per market (symbol) and per asset class → most profitable markets and
  the ones needing improvement;
- PnL per monthly period → when the bot gained significantly vs lost;
- audit-driven **per-market parameter recommendations** (entry threshold,
  stop-loss, take-profit) ready for the `market_tuning` setting.

Usage:
    python3 scripts/profit_audit.py [path/to/quantum_trade.db] [balance] [--json]
"""
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, Optional

# Make the repo root importable regardless of the current working directory,
# so `from api.engines.capital_profiles import ...` always resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from api.engines.capital_profiles import recommend_from_audit
except Exception:  # allow the script to run as a standalone tool
    recommend_from_audit = None

try:
    from api.engines.market_tuning import (recommend_for_market,  # noqa: F401
                                           build_tuning_from_audit)
except Exception:  # allow the script to run as a standalone tool
    recommend_for_market = None
    build_tuning_from_audit = None

try:
    from api.engines.market_universe import MarketUniverse
    _UNIVERSE = MarketUniverse()
except Exception:
    _UNIVERSE = None

ASSUMED_ROUND_TRIP_COSTS_PCT = 0.20   # 2 × (0.05 % fee + 0.05 % slippage)
MAX_COST_RATIO = 0.5                  # same default as the bot's cost filter
MIN_TRADES_FOR_VERDICT = 10           # statistical honesty per market


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    # metadata is stored as a JSON string in the DB
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except json.JSONDecodeError:
            d["metadata"] = {}
    return d


def _asset_class_of(symbol: Optional[str]) -> str:
    """Map a market id to its asset class via the universe (UNKNOWN fallback)."""
    if not symbol or _UNIVERSE is None:
        return "UNKNOWN"
    try:
        info = _UNIVERSE.get_info(symbol) or {}
        return info.get("asset_class", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def _month_of(close_time: Optional[str]) -> str:
    """Extract 'YYYY-MM' from an ISO close_time ("" when unparsable)."""
    if not close_time:
        return ""
    m = re.match(r"(\d{4})-(\d{2})", str(close_time).strip())
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def _aggregate(trades: list) -> Dict[str, Any]:
    """Aggregate a list of closed-trade dicts (shared by market/class/period)."""
    agg: Dict[str, Any] = {
        "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
        "gross_win": 0.0, "gross_loss": 0.0, "cost_leaks": 0,
    }
    for t in trades:
        pnl = float(t.get("pnl") or 0.0)
        agg["trades"] += 1
        agg["pnl"] += pnl
        if pnl > 0:
            agg["wins"] += 1
            agg["gross_win"] += pnl
        else:
            agg["losses"] += 1
            agg["gross_loss"] += abs(pnl)
        try:
            entry, sl = float(t.get("entry_price") or 0), float(t.get("sl") or 0)
            risk = abs(entry - sl)
            if risk > 0 and (ASSUMED_ROUND_TRIP_COSTS_PCT / (risk / entry * 100)) > MAX_COST_RATIO:
                agg["cost_leaks"] += 1
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    n = agg["trades"]
    win_rate = (agg["wins"] / n * 100) if n else 0.0
    avg_win = agg["gross_win"] / agg["wins"] if agg["wins"] else 0.0
    avg_loss = agg["gross_loss"] / agg["losses"] if agg["losses"] else 0.0
    realized_rr = (avg_win / avg_loss) if avg_loss > 0 else None
    return {
        "trades": n,
        "wins": agg["wins"],
        "losses": agg["losses"],
        "win_rate": round(win_rate, 1),
        "net_pnl": round(agg["pnl"], 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "realized_rr": round(realized_rr, 2) if realized_rr is not None else None,
        "expectancy_per_trade": round(agg["pnl"] / n, 2) if n else 0.0,
        "cost_leaks": agg["cost_leaks"],
        "verdict": (
            "PROFITABLE" if agg["pnl"] > 0 and win_rate >= 45 else
            ("LOSING" if agg["pnl"] < 0 or win_rate < 45 else "BREAKEVEN")
        ),
    }


def analyze_db(db_path: str) -> Dict[str, Any]:
    """Aggregate all CLOSED trades into per-mode / per-strategy statistics
    (LOT P) + per-market / per-asset-class / per-period statistics (LOT R)."""
    try:
        con = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        return {"error": f"cannot open database: {e}"}
    con.row_factory = sqlite3.Row
    trades = []
    try:
        rows = con.execute("SELECT * FROM trades WHERE status = 'CLOSED'").fetchall()
        trades = [_row_to_dict(r) for r in rows]
    except sqlite3.Error as e:
        con.close()
        return {"error": f"cannot read trades table: {e}"}
    con.close()

    stats: Dict[str, Any] = {"modes": {}}
    all_by_market: Dict[str, list] = {}
    all_by_class: Dict[str, list] = {}
    all_by_period: Dict[str, list] = {}
    for mode in sorted({t.get("mode", "?") for t in trades}):
        mode_trades = [t for t in trades if t.get("mode") == mode]
        per_strategy: Dict[str, list] = {}
        by_market: Dict[str, list] = {}
        for t in mode_trades:
            strat = (t.get("metadata") or {}).get("strategy", "unknown")
            per_strategy.setdefault(strat, []).append(t)
            market = t.get("symbol") or "unknown"
            by_market.setdefault(market, []).append(t)
            all_by_market.setdefault(market, []).append(t)
            all_by_class.setdefault(_asset_class_of(market), []).append(t)
            month = _month_of(t.get("close_time") or t.get("open_time"))
            if month:
                all_by_period.setdefault(month, []).append(t)

        mode_pnl = sum(float(t.get("pnl") or 0.0) for t in mode_trades)
        stats["modes"][mode] = {
            "closed_trades": len(mode_trades),
            "net_pnl": round(mode_pnl, 2),
            "by_strategy": {strat: _aggregate(ts) for strat, ts in per_strategy.items()},
            "by_market": {m: {**_aggregate(ts), "asset_class": _asset_class_of(m)}
                          for m, ts in sorted(by_market.items())},
        }

    stats["total_closed_trades"] = len(trades)
    stats["total_net_pnl"] = round(sum(float(t.get("pnl") or 0.0) for t in trades), 2)
    stats["assumed_round_trip_costs_pct"] = ASSUMED_ROUND_TRIP_COSTS_PCT

    # ---- LOT R: cross-mode per-market / per-class / per-period views ------- #
    stats["by_market"] = {m: _aggregate(ts) for m, ts in sorted(all_by_market.items())}
    for m, s in stats["by_market"].items():
        s["asset_class"] = _asset_class_of(m)
    stats["by_asset_class"] = {c: _aggregate(ts) for c, ts in sorted(all_by_class.items())}
    stats["by_period"] = {p: _aggregate(ts) for p, ts in sorted(all_by_period.items())}
    stats["min_trades_for_market_verdict"] = MIN_TRADES_FOR_VERDICT
    return stats


def print_report(stats: Dict[str, Any]) -> None:
    if "error" in stats:
        print(f"ERROR: {stats['error']}")
        return
    print("=" * 78)
    print("QUANTUM TRADE PRO — PROFIT AUDIT")
    print("=" * 78)
    for mode, m in sorted(stats["modes"].items()):
        print(f"\n[{mode}] closed trades: {m['closed_trades']} | net PnL: {m['net_pnl']:+.2f}")
        if not m["by_strategy"]:
            print("  (no closed trades)")
            continue
        print(f"  {'strategy':<12} {'trades':>6} {'win%':>6} {'PnL':>10} "
              f"{'avgW':>8} {'avgL':>8} {'RR':>6} {'exp':>8} {'leaks':>6}  verdict")
        print("  " + "-" * 74)
        for strat, s in sorted(m["by_strategy"].items()):
            print(f"  {strat:<12} {s['trades']:>6} {s['win_rate']:>5.1f}% "
                  f"{s['net_pnl']:>+10.2f} {s['avg_win']:>+8.2f} {s['avg_loss']:>+8.2f} "
                  f"{str(s['realized_rr']):>6} {s['expectancy_per_trade']:>+8.2f} "
                  f"{s['cost_leaks']:>6}  {s['verdict']}")
        by_market = m.get("by_market") or {}
        if by_market:
            print("\n  — per market —")
            print(f"  {'market':<16} {'class':<12} {'trades':>6} {'win%':>6} "
                  f"{'PnL':>10} {'RR':>6} {'leaks':>6}  verdict")
            print("  " + "-" * 74)
            for mid, s in sorted(by_market.items(), key=lambda kv: kv[1]["net_pnl"]):
                print(f"  {mid:<16} {s.get('asset_class', '?'):<12} {s['trades']:>6} "
                      f"{s['win_rate']:>5.1f}% {s['net_pnl']:>+10.2f} "
                      f"{str(s['realized_rr']):>6} {s['cost_leaks']:>6}  {s['verdict']}")

    # ---- LOT R: cross-mode market / class / period views -------------------- #
    by_market = stats.get("by_market") or {}
    if by_market:
        ranked = sorted(by_market.items(), key=lambda kv: kv[1]["net_pnl"])
        print("\n" + "-" * 78)
        print("MARKET RANKING (least profitable → most profitable)")
        print("-" * 78)
        for mid, s in ranked:
            tag = "" if s["trades"] >= MIN_TRADES_FOR_VERDICT else \
                f"  (< {MIN_TRADES_FOR_VERDICT} trades: no verdict)"
            print(f"  {mid:<16} {s.get('asset_class', '?'):<12} "
                  f"PnL {s['net_pnl']:>+9.2f}  win {s['win_rate']:>5.1f}%  "
                  f"RR {str(s['realized_rr']):>5}{tag}")

    by_class = stats.get("by_asset_class") or {}
    if by_class:
        print("\n" + "-" * 78)
        print("BY ASSET CLASS")
        print("-" * 78)
        for cls, s in sorted(by_class.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True):
            print(f"  {cls:<12} trades {s['trades']:>4}  PnL {s['net_pnl']:>+9.2f}  "
                  f"win {s['win_rate']:>5.1f}%  RR {str(s['realized_rr']):>5}  leaks {s['cost_leaks']}")

    by_period = stats.get("by_period") or {}
    if by_period:
        print("\n" + "-" * 78)
        print("BY PERIOD (month) — significant gains vs losses")
        print("-" * 78)
        for period, s in by_period.items():
            bar_len = min(40, int(abs(s["net_pnl"]) / 5))
            bar = ("+" * bar_len) if s["net_pnl"] >= 0 else ("-" * bar_len)
            print(f"  {period}  trades {s['trades']:>4}  PnL {s['net_pnl']:>+9.2f}  "
                  f"win {s['win_rate']:>5.1f}%  {bar}")

    print("\n" + "=" * 78)
    print(f"TOTAL closed trades: {stats['total_closed_trades']} | "
          f"net PnL: {stats['total_net_pnl']:+.2f}")
    print(f"(assumed round-trip costs for leak detection: {stats['assumed_round_trip_costs_pct']}%)")
    print("\nLecture:")
    print("  - win% < 45 et/ou exp < 0 → la stratégie/le marché perd structurellement (sélectivité à revoir)")
    print("  - leaks > 0 → trades dont les frais dépassaient 50% du risque (mathematiquement perdants)")
    print("  - RR réalisé < 1.5 → les sorties coupent les gains (trailing trop serré / TP trop tôt)")
    print("=" * 78)


def print_recommendations(stats: Dict[str, Any], balance: float = 0.0) -> None:
    """Print audit-driven optimization recommendations (capital-aware)."""
    if recommend_from_audit is None:
        print("\n[optimization] api.engines.capital_profiles unavailable — skipping.")
        return
    rec = recommend_from_audit(stats, balance)
    print("\n" + "-" * 78)
    print("AUDIT-DRIVEN OPTIMIZATION RECOMMENDATIONS")
    print("-" * 78)
    print(f"Account bracket: {rec['bracket']} (balance ≈ {rec.get('account_balance', balance):+.2f})")
    print(f"Targets (realistic): win rate >= {rec['targets']['min_win_rate_pct']}%, "
          f"RR >= {rec['targets']['min_realized_rr']}, "
          f"expectancy >= {rec['targets']['min_expectancy_r']}R, "
          f"profit factor >= {rec['targets']['min_profit_factor']}.")
    print(f"Health verdict: {rec['health_verdict']}")
    if rec.get("best_strategy", {}).get("name"):
        best = rec["best_strategy"]
        print(f"Best strategy: {best['name']} (expectancy {best['expectancy']:+.3f}R)")
    print("\nRecommended settings (start from these, then refine):")
    for k, v in rec["recommended_settings"].items():
        print(f"  {k:<22} {v}")
    if rec.get("per_strategy"):
        print("\nPer-strategy actions:")
        for strat, r in rec["per_strategy"].items():
            print(f"  {strat:<12} {r['verdict']:<10} -> {r['action']}")
            print(f"      {r['recommend']}")
    print("-" * 78)


def print_market_recommendations(stats: Dict[str, Any], balance: float = 0.0) -> Optional[Dict[str, Any]]:
    """LOT R: per-market parameter recommendations (entry threshold / SL / TP).

    Returns the full `market_tuning` map ready to paste into the
    `market_tuning` setting (or None when the module is unavailable).
    """
    by_market = stats.get("by_market") or {}
    if recommend_for_market is None:
        print("\n[per-market optimization] api.engines.market_tuning unavailable — skipping.")
        return None
    print("\n" + "-" * 78)
    print("PER-MARKET OPTIMIZATION (entry threshold / stop-loss / take-profit)")
    print("-" * 78)
    if not by_market:
        print("  (no closed trades — run again once the bot has traded)")
    for mid, s in sorted(by_market.items(), key=lambda kv: kv[1]["net_pnl"]):
        r = recommend_for_market(mid, s, balance)
        if r["verdict"] == "INSUFFICIENT_DATA":
            print(f"  {mid:<16} {r['verdict']:<12} (trades: {r['trades']})")
            continue
        p = r.get("params", {})
        extras = []
        if "min_score" in p:
            extras.append(f"min_score={p['min_score']}")
        if "risk_reward" in p:
            extras.append(f"TP={p['risk_reward']}R")
        if "atr_stop_multiplier" in p:
            extras.append(f"stop={p['atr_stop_multiplier']}×ATR")
        if "max_cost_ratio" in p:
            extras.append(f"max_cost_ratio={p['max_cost_ratio']}")
        print(f"  {mid:<16} {r['verdict']:<12} -> {r['action']:<32} [{', '.join(extras)}]")
        print(f"      {r['recommend']}")
    tuning = None
    if build_tuning_from_audit is not None:
        tuning = build_tuning_from_audit(by_market, balance, _UNIVERSE)
        if by_market:
            print("\n  → copier ce JSON dans le réglage `market_tuning` (/api/settings) :")
            print("    " + json.dumps({k: tuning[k] for k in sorted(tuning)
                                       if k in by_market and by_market[k]["trades"] >= MIN_TRADES_FOR_VERDICT}))
    print("-" * 78)
    return tuning


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    fee_idx = next((i for i, a in enumerate(args) if a in ("--fee", "--fee-pct")), None)
    if fee_idx is not None:
        try:
            fee_val = float(args[fee_idx + 1])
        except (IndexError, TypeError, ValueError):
            print("error: --fee requires a numeric percentage (e.g. 0.10)", file=sys.stderr)
            return 2
        global ASSUMED_ROUND_TRIP_COSTS_PCT
        ASSUMED_ROUND_TRIP_COSTS_PCT = fee_val
        del args[fee_idx:fee_idx + 2]
    db_path = args[0] if args else "data/quantum_trade.db"
    try:
        balance = float(args[1]) if len(args) > 1 else 0.0
    except (TypeError, ValueError, OverflowError):
        print("error: balance must be numeric", file=sys.stderr)
        return 2
    if balance < 0 or balance == float("inf") or balance != balance:
        print("error: balance must be a finite number >= 0", file=sys.stderr)
        return 2
    stats = analyze_db(db_path)
    if as_json:
        if "error" in stats:
            print(json.dumps(stats))
            return 1
        # keep the JSON self-contained for optimize_params / market_tuning
        stats["per_market_recommendations"] = {
            mid: recommend_for_market(mid, s, balance)
            for mid, s in (stats.get("by_market") or {}).items()
        } if recommend_for_market is not None else {}
        print(json.dumps(stats, default=str))
        return 0
    print_report(stats)
    if "error" in stats:
        return 1
    print_recommendations(stats, balance)
    print_market_recommendations(stats, balance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
