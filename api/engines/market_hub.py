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
    row["realtime_source"] = bool(
        scan.get("realtime_source") if "realtime_source" in scan
        else row.get("realtime_source", False)
    )
    row["active_source"] = scan.get("active_source") or row.get("active_source") or row.get("source")
    row["strategy"] = scan.get("strategy") or (scan.get("signal_data") or {}).get("strategy") or row.get("strategy")
    row["change"] = float(change or 0)
    row["sparkline"] = row.get("sparkline") or synthetic_sparkline(price, row["change"])
    row["display_symbol"] = row.get("display_symbol") or scan.get("display_symbol")
    row["name"] = row.get("name") or scan.get("name")
    row["underlying"] = row.get("underlying") or scan.get("underlying") or mid
    row["market_id"] = mid
    return row


def enrich_overview(overview: Dict[str, List[Dict[str, Any]]], scan: List[Dict[str, Any]] = None,
                    sort: str = "score", order: str = "desc") -> Dict[str, List[Dict[str, Any]]]:
    """Merge scan data by market id and expose one row per underlying."""
    scan_by = {asset.get("symbol"): asset for asset in (scan or [])}
    categories = list((overview or {}).keys())
    all_rows = []
    for category, items in (overview or {}).items():
        for item in items:
            row = enrich_market_item(item, scan_by)
            row["_category"] = category
            all_rows.append(row)

    selected: Dict[str, Dict[str, Any]] = {}
    for row in all_rows:
        key = str(row.get("underlying") or row.get("market_id"))
        current = selected.get(key)
        if current is None or _source_rank(row) > _source_rank(current):
            selected[key] = row

    out = {category: [] for category in categories}
    for row in selected.values():
        category = row.pop("_category", None)
        if category in out:
            out[category].append(row)
    descending = str(order or "desc").lower() != "asc"
    for category in out:
        out[category] = sort_hub_items(out[category], sort, descending)
    return out


def _source_rank(row: Dict[str, Any]):
    try:
        freshness = -float(row.get("data_age_ms"))
    except (TypeError, ValueError):
        freshness = float("-inf")
    return bool(row.get("realtime_source")), freshness, float(row.get("score") or 0)


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
