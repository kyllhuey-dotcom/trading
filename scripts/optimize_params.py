#!/usr/bin/env python3
"""
Capital-aware parameter optimizer — Quantum Trade Pro.

Given an account balance (and optionally an audit report) it prints the
parameters that should fit that capital tier, together with the audit-driven
recommendations (win-rate / RR / expectancy based).

Usage:
    python3 scripts/optimize_params.py [balance] [audit_stats.json?]

Examples:
    python3 scripts/optimize_params.py 5.0
    python3 scripts/optimize_params.py 120.0 audit.json
"""
import json
import os
import sys
from typing import Any, Dict, Optional

# Make the repo root importable regardless of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from api.engines.capital_profiles import (profile_overrides, resolve_bracket,
                                              recommend_from_audit, bracket_summary)
except Exception:  # allow standalone use
    profile_overrides = resolve_bracket = None
    recommend_from_audit = None
    bracket_summary = None

try:
    from api.engines.market_tuning import markets_feasible_for_capital
    from api.engines.market_universe import MarketUniverse
except Exception:  # allow standalone use
    markets_feasible_for_capital = None
    MarketUniverse = None


def load_audit(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        print(f"[warn] cannot read audit JSON ({e}) — using defaults", file=sys.stderr)
        return None


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        balance = float(args[0]) if args else 0.0
    except (TypeError, ValueError, OverflowError):
        print("error: balance must be numeric", file=sys.stderr)
        return 2
    if balance < 0 or balance == float("inf") or balance != balance:
        print("error: balance must be a finite number >= 0", file=sys.stderr)
        return 2
    audit_path = args[1] if len(args) > 1 else None
    audit = load_audit(audit_path)

    print("=" * 78)
    print("QUANTUM TRADE PRO — CAPITAL-AWARE PARAMETER OPTIMIZER")
    print("=" * 78)

    if resolve_bracket is not None:
        b = resolve_bracket(balance)
        print(f"\nAccount balance : {balance:+.2f}")
        print(f"Capital bracket : {b.name} "
              f"([{b.min_balance} — {b.max_balance if b.max_balance != float('inf') else '∞'}])")
        print(f"Note            : {b.note}")
        print("\nBracket parameter profile:")
        for k, v in profile_overrides(balance).items():
            print(f"  {k:<24} {v}")
    else:
        print("\napi.engines.capital_profiles unavailable — cannot resolve bracket.")

    if recommend_from_audit is not None:
        rec = recommend_from_audit(audit, balance)
        print("\nAudit-driven recommendations:")
        print(f"  Best strategy : {rec.get('best_strategy', {}).get('name')}")
        print(f"  Health verdict: {rec['health_verdict']}")
        for k, v in rec["recommended_settings"].items():
            print(f"    {k:<24} {v}")
        if rec.get("per_strategy"):
            print("  Per-strategy actions:")
            for strat, r in rec["per_strategy"].items():
                print(f"    {strat:<14} {r['verdict']:<10} -> {r['action']}")
                print(f"        {r['recommend']}")

    # ---- LOT R: which markets actually work at this capital level ---------- #
    if markets_feasible_for_capital is not None and MarketUniverse is not None:
        try:
            lev_cap = 10.0
            if profile_overrides is not None:
                lev_cap = float(profile_overrides(balance).get("max_leverage", 10.0))
            feasibility = markets_feasible_for_capital(balance, MarketUniverse(), leverage_cap=lev_cap)
            print(f"\nMarkets feasible at this balance (REAL-mode estimate, {lev_cap:.0f}x cap):")
            for cls, c in sorted(feasibility["asset_classes"].items(),
                                 key=lambda kv: kv[1]["min_capital_estimate"] or 9e9):
                status = "OK" if c["class_feasible"] else "capital too small"
                cheapest = ", ".join(x["market_id"] for x in c["cheapest"][:4])
                print(f"  {cls:<12} {status:<18} "
                      f"min capital ~ {c['min_capital_estimate'] if c['min_capital_estimate'] is not None else '?'} $ "
                      f"({c['markets_feasible']}/{c['markets_total']} markets — e.g. {cheapest})")
            print("  Note: in DEMO (paper) every market is feasible; Yahoo-sourced classes")
            print("        (delayed data) stay blocked for automated execution by default.")
        except Exception as e:
            print(f"\n[warn] market feasibility unavailable: {e}", file=sys.stderr)

    print("\nPer-market tuning: run `python3 scripts/profit_audit.py <db> "
          f"{balance} --json` once the bot has closed trades, then apply the")
    print("`market_tuning` overrides it produces (entry threshold / SL / TP per market).")
    print("\n" + "=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
