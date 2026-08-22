#!/usr/bin/env python3
"""
Profit audit — Quantum Trade Pro (LOT P).

Reads a bot database (default data/quantum_trade.db, or any path — e.g. your
Railway volume export) and reports, per mode and per strategy:
- number of closed trades, win rate, net PnL, avg win / avg loss;
- expectancy per trade and realized reward:risk;
- trades whose round-trip costs were too high vs the risk taken (leaks);
- warnings and a profitability verdict per strategy.

Usage:
    python3 scripts/profit_audit.py [path/to/quantum_trade.db]
"""
import json
import os
import sqlite3
import sys
from typing import Any, Dict

# Make the repo root importable regardless of the current working directory,
# so `from api.engines.capital_profiles import ...` always resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from api.engines.capital_profiles import recommend_from_audit
except Exception:  # allow the script to run as a standalone tool
    recommend_from_audit = None

ASSUMED_ROUND_TRIP_COSTS_PCT = 0.20   # 2 × (0.05 % fee + 0.05 % slippage)
MAX_COST_RATIO = 0.5                  # same default as the bot's cost filter


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    # metadata is stored as a JSON string in the DB
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except json.JSONDecodeError:
            d["metadata"] = {}
    return d


def analyze_db(db_path: str) -> Dict[str, Any]:
    """Aggregate all CLOSED trades into per-mode / per-strategy statistics."""
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
    for mode in sorted({t.get("mode", "?") for t in trades}):
        mode_trades = [t for t in trades if t.get("mode") == mode]
        per_strategy: Dict[str, Dict[str, Any]] = {}
        for t in mode_trades:
            strat = (t.get("metadata") or {}).get("strategy", "unknown")
            s = per_strategy.setdefault(strat, {
                "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                "gross_win": 0.0, "gross_loss": 0.0, "cost_leaks": 0, "total": 0,
            })
            pnl = float(t.get("pnl") or 0.0)
            s["trades"] += 1
            s["total"] += 1
            s["pnl"] += pnl
            if pnl > 0:
                s["wins"] += 1
                s["gross_win"] += pnl
            else:
                s["losses"] += 1
                s["gross_loss"] += abs(pnl)
            # Cost-ratio leak detection: entry vs SL vs assumed round-trip costs
            try:
                entry, sl = float(t.get("entry_price") or 0), float(t.get("sl") or 0)
                risk = abs(entry - sl)
                if risk > 0 and (ASSUMED_ROUND_TRIP_COSTS_PCT / (risk / entry * 100)) > MAX_COST_RATIO:
                    s["cost_leaks"] += 1
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        per_strategy_out = {}
        for strat, s in per_strategy.items():
            n = s["trades"]
            win_rate = (s["wins"] / n * 100) if n else 0.0
            avg_win = s["gross_win"] / s["wins"] if s["wins"] else 0.0
            avg_loss = s["gross_loss"] / s["losses"] if s["losses"] else 0.0
            realized_rr = (avg_win / avg_loss) if avg_loss > 0 else None
            expectancy = (s["pnl"] / n) if n else 0.0
            per_strategy_out[strat] = {
                "trades": n,
                "wins": s["wins"],
                "losses": s["losses"],
                "win_rate": round(win_rate, 1),
                "net_pnl": round(s["pnl"], 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "realized_rr": round(realized_rr, 2) if realized_rr is not None else None,
                "expectancy_per_trade": round(expectancy, 2),
                "cost_leaks": s["cost_leaks"],
                "verdict": (
                    "PROFITABLE" if s["pnl"] > 0 and win_rate >= 45 else
                    ("LOSING" if s["pnl"] < 0 or win_rate < 45 else "BREAKEVEN")
                ),
            }
        mode_pnl = sum(float(t.get("pnl") or 0.0) for t in mode_trades)
        stats["modes"][mode] = {
            "closed_trades": len(mode_trades),
            "net_pnl": round(mode_pnl, 2),
            "by_strategy": per_strategy_out,
        }
    stats["total_closed_trades"] = len(trades)
    stats["total_net_pnl"] = round(sum(float(t.get("pnl") or 0.0) for t in trades), 2)
    stats["assumed_round_trip_costs_pct"] = ASSUMED_ROUND_TRIP_COSTS_PCT
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
    print("\n" + "=" * 78)
    print(f"TOTAL closed trades: {stats['total_closed_trades']} | "
          f"net PnL: {stats['total_net_pnl']:+.2f}")
    print(f"(assumed round-trip costs for leak detection: {stats['assumed_round_trip_costs_pct']}%)")
    print("\nLecture:")
    print("  - win% < 45 et/ou exp < 0 → la stratégie perd structurellement (sélectivité à revoir)")
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
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
    print_report(stats)
    if "error" in stats:
        return 1
    print_recommendations(stats, balance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
