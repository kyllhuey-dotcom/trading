"""Institutional ≥80-only candidate selection (v2.7).

v2.7 changes:
- Enforces AUTO_EXECUTION_SCORE_FLOOR on all paths
- Integrates with OpportunityRanker for single best selection
- select_candidates remains for backward compat but enforces floor
"""
from typing import Any, Dict, List, Set

from .constants import AUTO_EXECUTION_SCORE_FLOOR


def select_candidates(results: List[Dict[str, Any]], min_score: float,
                      active_symbols: Set[str], max_positions: int,
                      _legacy_min_score: float = 0) -> List[Dict[str, Any]]:
    """Select candidates for execution.

    v2.7: min_score is always floored to AUTO_EXECUTION_SCORE_FLOOR (80).
    Even if settings/profile/tuning try to lower it, the floor holds.
    """
    # v2.7: enforce the inviolable floor
    effective_min = max(float(AUTO_EXECUTION_SCORE_FLOOR), float(min_score))

    active = set(active_symbols or [])
    remaining = max(0, int(max_positions) - len(active))
    cands = []
    for r in results or []:
        if not r.get("tradable"):
            continue
        # v2.7 P0-5: arbitrage strategies are not auto-executable
        sig = r.get("signal_data") or {}
        if sig.get("tradable") is False:
            continue
        score = float(r.get("score") or 0)
        if score < effective_min:
            continue
        if not sig.get("market_id") or not sig.get("entry"):
            continue
        sym = r.get("symbol")
        if sym in active:
            continue
        cands.append(r)
    cands.sort(key=lambda a: float(a.get("score") or 0), reverse=True)
    return cands[:remaining]


def describe_intent(running: bool, armed: bool, n_candidates: int, n_active: int,
                    max_positions: int, min_score: float) -> Dict[str, Any]:
    if not running:
        return {"code": "STOPPED", "state": "STOPPED", "message": "System stopped"}
    if not armed:
        return {"code": "DISARMED", "state": "WAITING_SETUP", "message": "Execution disarmed"}
    if n_active >= int(max_positions):
        return {"code": "FULL", "state": "WAITING_SETUP",
                "message": f"All {max_positions} slots filled"}
    if n_candidates <= 0:
        effective = max(int(AUTO_EXECUTION_SCORE_FLOOR), int(min_score))
        return {
            "code": "IDLE",
            "state": "WAITING_SETUP",
            "message": f"Waiting for institutional setup ≥ {effective}",
        }
    return {"code": "EXECUTING", "state": "EXECUTING",
            "message": "Executing high-conviction trade…"}
