"""Global Radar helpers — pure, testable (LOT 1)."""
from typing import Any, Dict, List, Optional


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
        return [a for a in out if float(a.get("score") or 0) >= 80]
    if mode in ("ge90", ">=90"):
        return [a for a in out if float(a.get("score") or 0) >= 90]
    if mode == "crypto":
        return [a for a in out if str(a.get("asset_class") or "").upper() == "CRYPTO"]
    return out


def enrich_radar_row(asset: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(asset or {})
    sig = row.get("signal_data") or {}
    row["display_symbol"] = row.get("display_symbol") or sig.get("display_symbol") or str(row.get("symbol") or "").upper().replace("_", "/")
    row["strategy"] = row.get("strategy") or sig.get("strategy") or "structure"
    row["direction"] = row.get("direction") or sig.get("direction") or row.get("trend")
    row["data_age_label"] = format_data_age(row.get("data_age_ms"))
    return row


def prepare_radar(assets: List[Dict[str, Any]], sort: str = "score", order: str = "desc",
                  filter_mode: str = "all") -> List[Dict[str, Any]]:
    filtered = filter_assets(assets, filter_mode)
    descending = str(order or "desc").lower() != "asc"
    key = sort or "score"
    sorted_rows = sort_assets(filtered, key=key, descending=descending)
    return [enrich_radar_row(a) for a in sorted_rows]
