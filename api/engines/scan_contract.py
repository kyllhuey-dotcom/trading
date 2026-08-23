"""Universe-complete scanner / radar / hub rows.

Markets without a ticker stay visible as DATA_UNAVAILABLE. Nothing in the
tracked universe is silently dropped.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .constants import AUTO_EXECUTION_SCORE_FLOOR, DEFAULT_RSI_RISK_REWARD


def placeholder_row(
    symbol: str,
    info: Optional[Dict[str, Any]] = None,
    *,
    status: str = "DATA_UNAVAILABLE",
    reason: str = "DATA_UNAVAILABLE",
    block_reason: str = "DATA_UNAVAILABLE",
) -> Dict[str, Any]:
    info = info or {}
    display = info.get("display_symbol") or str(symbol or "").upper().replace("_", "/")
    return {
        "symbol": symbol,
        "display_symbol": display,
        "underlying": info.get("underlying", symbol),
        "asset_class": info.get("asset_class", "UNKNOWN"),
        "name": info.get("name", display),
        "strategy": "rsi",
        "signal": "NO_TRADE",
        "status": status,
        "tradable": False,
        "reason": reason,
        "block_reason": block_reason,
        "score": 0,
        "price": None,
        "change": None,
        "spread": None,
        "volume": None,
        "data_age_ms": None,
        "realtime_source": False,
        "diagnosis": {
            "main_blocker": block_reason,
            "main_reason": reason,
            "checks": {"DATA_VALID": "FAIL"},
        },
        "signal_data": {
            "status": "NO_TRADE",
            "direction": None,
            "score": 0,
            "reason": reason,
            "entry": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "market_id": symbol,
            "strategy": "rsi",
            "rsi": 0.0,
            "ema8": 0.0,
            "ema21": 0.0,
            "vol_ratio": None,
            "risk_reward": DEFAULT_RSI_RISK_REWARD,
            "metadata": {},
        },
    }


def merge_universe_rows(
    rows: Optional[Iterable[Dict[str, Any]]],
    universe: Any,
    *,
    missing_reason: str = "DATA_UNAVAILABLE",
) -> List[Dict[str, Any]]:
    """Keep every universe market visible, filling gaps with placeholders."""
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        symbol = row.get("symbol") or row.get("market_id")
        if not symbol:
            continue
        merged = dict(row)
        merged.setdefault("strategy", "rsi")
        merged.setdefault("signal", merged.get("signal") or "NO_TRADE")
        merged.setdefault("tradable", False)
        if not merged.get("status"):
            merged["status"] = "DATA_UNAVAILABLE"
        if not merged.get("display_symbol"):
            info = universe.get_info(symbol) if universe else None
            merged["display_symbol"] = (info or {}).get("display_symbol") or str(symbol).upper()
        by_symbol[str(symbol)] = merged

    ids = list(universe.get_all_ids()) if universe and hasattr(universe, "get_all_ids") else []
    out: List[Dict[str, Any]] = []
    for symbol in ids:
        if symbol in by_symbol:
            out.append(by_symbol.pop(symbol))
        else:
            info = universe.get_info(symbol) if universe else None
            out.append(placeholder_row(symbol, info, reason=missing_reason,
                                       block_reason=missing_reason))
    # Preserve unexpected extra rows (tests / injected fixtures).
    out.extend(by_symbol.values())
    return out


def summarize_scan(rows: Optional[Iterable[Dict[str, Any]]], total: int = 0) -> Dict[str, Any]:
    assets = list(rows or [])
    unavailable = sum(1 for row in assets if str(row.get("status") or "").upper() == "DATA_UNAVAILABLE")
    errors = sum(1 for row in assets if str(row.get("status") or "").upper() == "ERROR")
    available = sum(
        1 for row in assets
        if str(row.get("status") or "").upper() in {"LIVE", "DELAYED", "STALE"}
    )
    signals = sum(
        1 for row in assets
        if str(row.get("signal") or (row.get("signal_data") or {}).get("status")) == "SIGNAL_DETECTED"
    )
    tradable = sum(1 for row in assets if row.get("tradable"))
    return {
        "markets_total": int(total or len(assets)),
        "markets_processed": len(assets),
        "markets_available": available,
        "markets_unavailable": unavailable,
        "markets_error": errors,
        "rsi_signals": signals,
        "markets_tradable": tradable,
        "signals_ge_floor": sum(1 for row in assets if float(row.get("score") or 0) >= AUTO_EXECUTION_SCORE_FLOOR),
    }


def classify_block_reason(
    *,
    running: bool = True,
    armed: bool = True,
    scanning: bool = False,
    scan_timeout: bool = False,
    ticker: Optional[Dict[str, Any]] = None,
    signal: Optional[Dict[str, Any]] = None,
    news: Optional[Dict[str, Any]] = None,
    diagnosis: Optional[Dict[str, Any]] = None,
    delayed: bool = False,
    quota: bool = False,
    provider_error: bool = False,
) -> str:
    if not running:
        return "SYSTEM_NOT_RUNNING"
    if not armed:
        return "ENGINE_DISARMED"
    if scan_timeout:
        return "SCAN_TIMEOUT"
    if scanning and not (signal or ticker):
        return "SCAN_IN_PROGRESS"
    if quota:
        return "PROVIDER_QUOTA_EXCEEDED"
    if provider_error:
        return "PROVIDER_ERROR"
    if not ticker:
        return "DATA_UNAVAILABLE"
    if delayed:
        return "NON_REALTIME_SOURCE"

    news = news or {}
    if news.get("status") == "DATA_UNAVAILABLE" and not news.get("news_ok", True):
        return "CALENDAR_UNAVAILABLE"
    if news.get("news_ok") is False:
        return "NEWS_BLOCKED"

    sig = signal or {}
    reason = str(sig.get("block_reason") or sig.get("reason") or "")
    upper = reason.upper()
    mapping = (
        ("INSUFFICIENT", "INSUFFICIENT_CANDLES"),
        ("RSI_NO_CROSS", "RSI_NO_CROSS"),
        ("DID NOT EXIT", "RSI_NO_CROSS"),
        ("PRICE", "PRICE_CONFIRMATION_MISSING"),
        ("VOLUME CONFIRMATION", "VOLUME_CONFIRMATION_MISSING"),
        ("VOLUME UNAVAILABLE", "EMA21_CONFIRMATION_MISSING"),
        ("EMA21", "EMA21_CONFIRMATION_MISSING"),
        ("BELOW MINIMUM SCORE", "SCORE_BELOW_84"),
        ("SCORE_BELOW_84", "SCORE_BELOW_84"),
        ("VOLATILE", "VOLATILE_THRESHOLD_89"),
        ("NEWS/SESSION", "NEWS_BLOCKED"),
        ("SPREAD", "SPREAD_TOO_HIGH"),
        ("LIQUIDITY", "LIQUIDITY_INVALID"),
        ("MARKET CLOSED", "MARKET_CLOSED"),
        ("CORRELATION", "CORRELATION_BLOCKED"),
        ("COST", "COST_GATE_BLOCKED"),
        ("RISK", "RISK_BLOCKED"),
    )
    for needle, code in mapping:
        if needle in upper:
            if "89" in reason or (sig.get("min_score_applied") or 0) >= 89:
                if "BELOW MINIMUM SCORE" in upper or "SCORE" in upper:
                    return "VOLATILE_THRESHOLD_89"
            return code

    checks = (diagnosis or {}).get("checks") or {}
    if checks.get("MARKET_OPEN") == "FAIL":
        return "MARKET_CLOSED"
    if checks.get("SPREAD_VALID") == "FAIL":
        return "SPREAD_TOO_HIGH"
    if checks.get("LIQUIDITY_VALID") == "FAIL":
        return "LIQUIDITY_INVALID"
    if checks.get("NEWS_CLEAR") == "FAIL":
        return "NEWS_BLOCKED"
    return str((diagnosis or {}).get("main_blocker") or "NO_TRADE")
