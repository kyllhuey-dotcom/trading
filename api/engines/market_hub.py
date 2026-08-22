"""Premium Market Hub helpers — pure (LOT 2)."""
from typing import Any, Dict, List


def synthetic_sparkline(price: float, change_pct: float, points: int = 12) -> List[float]:
    """Deterministic 24h-ish sparkline from last price + change %."""
    try:
        last = float(price or 0)
        ch = float(change_pct or 0) / 100.0
    except (TypeError, ValueError):
        return [0.0] * points
    if last <= 0:
        return [0.0] * points
    start = last / (1.0 + ch) if (1.0 + ch) != 0 else last
    out = []
    for i in range(points):
        t = i / max(1, points - 1)
        # slight sine wobble so the line isn't perfectly linear
        wobble = 1.0 + 0.004 * ((i % 3) - 1)
        out.append(round((start + (last - start) * t) * wobble, 8))
    out[-1] = last
    return out


def enrich_market_item(item: Dict[str, Any], scan_by_id: Dict[str, Any] = None) -> Dict[str, Any]:
    row = dict(item or {})
    mid = row.get("market_id") or row.get("symbol")
    scan = (scan_by_id or {}).get(mid) or {}
    price = float(row.get("price") or row.get("last") or scan.get("price") or 0)
    change = row.get("change")
    if change is None:
        change = row.get("change_24h")
    if change is None:
        change = scan.get("change") or 0
    row["price"] = price
    row["score"] = int(scan.get("score") or row.get("score") or 0)
    row["trend"] = scan.get("trend") or row.get("trend")
    row["data_age_ms"] = scan.get("data_age_ms") if scan.get("data_age_ms") is not None else row.get("data_age_ms")
    row["strategy"] = scan.get("strategy") or (scan.get("signal_data") or {}).get("strategy") or row.get("strategy")
    row["change"] = float(change or 0)
    row["sparkline"] = row.get("sparkline") or synthetic_sparkline(price, row["change"])
    row["display_symbol"] = row.get("display_symbol") or scan.get("display_symbol")
    row["name"] = row.get("name") or scan.get("name")
    row["market_id"] = mid
    return row


def enrich_overview(overview: Dict[str, List[Dict[str, Any]]], scan: List[Dict[str, Any]] = None,
                    sort: str = "score", order: str = "desc") -> Dict[str, List[Dict[str, Any]]]:
    scan_by = {}
    for a in scan or []:
        scan_by[a.get("symbol")] = a
    out = {}
    descending = str(order or "desc").lower() != "asc"
    for cat, items in (overview or {}).items():
        enriched = [enrich_market_item(it, scan_by) for it in items]
        out[cat] = sort_hub_items(enriched, sort, descending)
    return out


def sort_hub_items(items: List[Dict[str, Any]], key: str = "score", descending: bool = True) -> List[Dict[str, Any]]:
    key = key or "score"
    if key not in ("score", "volume", "change", "price", "symbol"):
        key = "score"

    def _val(a: Dict[str, Any]):
        if key == "symbol":
            return str(a.get("display_symbol") or a.get("symbol") or "").lower()
        v = a.get(key)
        if v is None:
            return float("-inf") if descending else float("inf")
        try:
            return float(v)
        except (TypeError, ValueError):
            return str(v).lower()

    return sorted(list(items or []), key=_val, reverse=bool(descending) and key != "symbol")
