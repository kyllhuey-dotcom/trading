"""Premium Market Hub helpers — pure (LOT 2).

v3.3.2 (D1/D2): 100 % real data.

- The ``synthetic_sparkline`` (a curve invented from the last price) has been
  REMOVED. A hub row only carries a sparkline when the scanner attached the
  REAL latest 1h OHLCV closes; otherwise the list is empty and the UI draws
  nothing. An empty chart is honest; a fake one is not.
- A missing price is ``None`` (rendered as "—"), never a fabricated ``0.0``.
"""
from typing import Any, Dict, List, Optional


def _to_float(value: Any) -> Optional[float]:
    """Coerce a raw quote field to float, or None (0.0 stays 0.0)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num_or_none(value: Any) -> Optional[float]:
    """Coerce a PRICE to a positive float, or None.

    No real tradable asset prices at 0 — a 0.0 here is a "no data" marker
    and must never be presented as a price (D2).
    """
    out = _to_float(value)
    if out is None or out <= 0:
        return None
    return out


def _real_sparkline(raw: Any, max_points: int = 24) -> List[float]:
    """v3.3.2 (D1): keep ONLY real closes, nothing else.

    Accepts the list the scanner stored (real 1h OHLCV closes). Non-numeric
    or non-positive entries are dropped; the result is capped to the latest
    ``max_points``. There is NO interpolation, padding or synthesis path —
    if the input is empty the output is empty.
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[float] = []
    for value in raw:
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.append(v)
    return out[-max_points:]


def enrich_market_item(item: Dict[str, Any], scan_by_id: Dict[str, Any] = None) -> Dict[str, Any]:
    row = dict(item or {})
    mid = row.get("market_id") or row.get("symbol")
    scan = (scan_by_id or {}).get(mid) or {}
    # v3.3.2 (D2): real price or None — a 0.0 fallback was presenting
    # "no data" as a $0.00 market.
    price = _num_or_none(
        row.get("price")
        if row.get("price") not in (None, "")
        else (row.get("last") if row.get("last") not in (None, "") else scan.get("price"))
    )
    change = _to_float(row.get("change"))
    if change is None:
        change = _to_float(row.get("change_24h"))
    if change is None:
        change = _to_float(scan.get("change"))
    if price is None:
        # No price → no meaningful change either (0.0 % would be fake).
        change = None
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
    row["change"] = change
    # v3.3.2 (D1): REAL sparkline only — the scanner's latest 1h OHLCV
    # closes. No sparkline in the item/scan → empty list, never invented.
    raw_spark = row.get("sparkline")
    if raw_spark is None:
        raw_spark = scan.get("sparkline")
    row["sparkline"] = _real_sparkline(raw_spark)
    row["sparkline_stale"] = bool(
        row.get("sparkline_stale")
        if row.get("sparkline_stale") is not None
        else scan.get("sparkline_stale", False)
    )
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
