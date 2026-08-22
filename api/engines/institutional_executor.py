"""Institutional ≥80-only candidate selection (LOT 8)."""
from typing import Any, Dict, List, Set


def select_candidates(results: List[Dict[str, Any]], min_score: float,
                      active_symbols: Set[str], max_positions: int) -> List[Dict[str, Any]]:
    active = set(active_symbols or [])
    remaining = max(0, int(max_positions) - len(active))
    cands = []
    for r in results or []:
        if not r.get("tradable"):
            continue
        score = float(r.get("score") or 0)
        if score < float(min_score):
            continue
        sig = r.get("signal_data") or {}
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
        return {"code": "STOPPED", "message": "System stopped"}
    if not armed:
        return {"code": "DISARMED", "message": "Execution disarmed"}
    if n_active >= int(max_positions):
        return {"code": "FULL", "message": f"All {max_positions} slots filled"}
    if n_candidates <= 0:
        return {
            "code": "IDLE",
            "message": f"Waiting for institutional setup ≥ {int(min_score)}",
        }
    return {"code": "EXECUTING", "message": "Executing high-conviction trade…"}
