"""Global Radar helpers — pure, testable (LOT 1)."""
from typing import Any, Dict, List, Optional

from .constants import AUTO_EXECUTION_SCORE_FLOOR


def format_data_age(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    try:
        val = float(ms)
    except (TypeError, ValueError):
        return "—"
    if val < 1000:
        return f"{int(val)}ms"
    if val < 60_000:
        return f"{val / 1000.0:.1f}s"
    return f"{val / 60_000.0:.1f}m"


def sort_assets(assets: List[Dict[str, Any]], key: str = "score", descending: bool = True) -> List[Dict[str, Any]]:
    def _val(a: Dict[str, Any]):
        v = a.get(key)
        if v is None:
            return float("-inf") if descending else float("inf")
        if isinstance(v, (int, float)):
            return v
        return str(v).lower()

    return sorted(list(assets or []), key=_val, reverse=bool(descending))


def filter_assets(assets: List[Dict[str, Any]], mode: str = "all") -> List[Dict[str, Any]]:
    mode = (mode or "all").lower()
    out = list(assets or [])
    if mode in ("ge80", ">=80"):
        # v2.8: the "institutional" filter follows the inviolable floor (84).
        return [a for a in out if float(a.get("score") or 0) >= AUTO_EXECUTION_SCORE_FLOOR]
    if mode in ("ge90", ">=90"):
        return [a for a in out if float(a.get("score") or 0) >= 90]
    if mode == "crypto":
        return [a for a in out if str(a.get("asset_class") or "").upper() == "CRYPTO"]
    if mode in ("live", "live_only"):
        return [a for a in out if bool(a.get("realtime_source"))]
    return out


def deduplicate_underlyings(assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep one row per exposure, preferring realtime then freshest data."""
    selected: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def rank(row: Dict[str, Any]):
        age = row.get("data_age_ms")
        try:
            age_rank = -float(age) if age is not None else float("-inf")
        except (TypeError, ValueError):
            age_rank = float("-inf")
        return bool(row.get("realtime_source")), age_rank, float(row.get("score") or 0)

    for asset in assets or []:
        key = str(asset.get("underlying") or asset.get("symbol") or asset.get("display_symbol"))
        if key not in selected:
            selected[key] = asset
            order.append(key)
        elif rank(asset) > rank(selected[key]):
            selected[key] = asset
    return [selected[key] for key in order]


def enrich_radar_row(asset: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(asset or {})
    sig = row.get("signal_data") or {}
    row["display_symbol"] = row.get("display_symbol") or sig.get("display_symbol") or str(row.get("symbol") or "").upper().replace("_", "/")
    row["strategy"] = row.get("strategy") or sig.get("strategy") or "structure"
    row["direction"] = row.get("direction") or sig.get("direction") or row.get("trend")
    # Radar rows are also an execution entry point. Legacy strategies may be
    # displayed for diagnostics, but their trade action is always disabled.
    row["auto_execution_allowed"] = str(row["strategy"]).lower() == "rsi"
    if not row["auto_execution_allowed"]:
        row["tradable"] = False
    row["data_age_label"] = format_data_age(row.get("data_age_ms"))
    row["data_source_label"] = "LIVE" if row.get("realtime_source") else "DELAYED"
    return row


def prepare_radar(assets: List[Dict[str, Any]], sort: str = "score", order: str = "desc",
                  filter_mode: str = "all", live_only: bool = False) -> List[Dict[str, Any]]:
    unique = deduplicate_underlyings(assets)
    filtered = filter_assets(unique, filter_mode)
    if live_only:
        filtered = [asset for asset in filtered if bool(asset.get("realtime_source"))]
    descending = str(order or "desc").lower() != "asc"
    key = sort or "score"
    sorted_rows = sort_assets(filtered, key=key, descending=descending)
    return [enrich_radar_row(a) for a in sorted_rows]
