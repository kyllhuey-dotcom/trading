"""Free-tier provider capabilities and quote freshness classification.

A provider name is never treated as proof of realtime data. LIVE / DELAYED /
STALE / ERROR / DATA_UNAVAILABLE are decided from the payload that was
actually received (timestamp, age, status and free-tier rights).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

# Conservative published free-tier budgets. These are used for client-side
# pacing only — they do not claim a paid realtime entitlement.
PROVIDER_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "twelvedata": {
        "provider_id": "twelvedata",
        "free_tier": True,
        "realtime_capable": False,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 8,
        "max_requests_per_day": 800,
    },
    "alpha_vantage": {
        "provider_id": "alpha_vantage",
        "free_tier": True,
        "realtime_capable": False,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 5,
        "max_requests_per_day": 25,
    },
    "finnhub": {
        "provider_id": "finnhub",
        "free_tier": True,
        "realtime_capable": False,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 30,
        "max_requests_per_day": 60,
    },
    "yahoo": {
        "provider_id": "yahoo",
        "free_tier": True,
        "realtime_capable": False,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 30,
        "max_requests_per_day": None,
    },
    "binance": {
        "provider_id": "binance",
        "free_tier": True,
        "realtime_capable": True,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 1200,
        "max_requests_per_day": None,
    },
    "bybit": {
        "provider_id": "bybit",
        "free_tier": True,
        "realtime_capable": True,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 120,
        "max_requests_per_day": None,
    },
    "okx": {
        "provider_id": "okx",
        "free_tier": True,
        "realtime_capable": True,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 120,
        "max_requests_per_day": None,
    },
    "kraken": {
        "provider_id": "kraken",
        "free_tier": True,
        "realtime_capable": True,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 60,
        "max_requests_per_day": None,
    },
    "coinbase": {
        "provider_id": "coinbase",
        "free_tier": True,
        "realtime_capable": True,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 30,
        "max_requests_per_day": None,
    },
    "gate": {
        "provider_id": "gate",
        "free_tier": True,
        "realtime_capable": True,
        "supports_ohlcv": True,
        "supports_quote": True,
        "supports_websocket": False,
        "max_requests_per_minute": 120,
        "max_requests_per_day": None,
    },
}

# Polygon/Massive and Marketstack are documented but not wired: their free
# plans are delayed and quota-limited in ways that would silently break the
# scanner if treated as realtime.
STUDIED_NOT_WIRED = {
    "polygon": {
        "provider_id": "polygon",
        "free_tier": True,
        "realtime_capable": False,
        "wired": False,
        "reason": "Free plan is delayed; not used for auto-trading",
    },
    "marketstack": {
        "provider_id": "marketstack",
        "free_tier": True,
        "realtime_capable": False,
        "wired": False,
        "reason": "Free plan is EOD/delayed; not used for auto-trading",
    },
}

REALTIME_PROVIDER_IDS = {
    pid for pid, cap in PROVIDER_CAPABILITIES.items() if cap.get("realtime_capable")
}

QUOTA_MARKERS = (
    "quota", "rate limit", "note", "thank you for using alpha vantage",
    "api call frequency", "exceeded", "too many requests",
)


def capabilities_for(provider_id: Optional[str]) -> Dict[str, Any]:
    key = _normalize_provider_id(provider_id)
    if key in PROVIDER_CAPABILITIES:
        return dict(PROVIDER_CAPABILITIES[key])
    if key.startswith("yahoo"):
        return dict(PROVIDER_CAPABILITIES["yahoo"])
    return {
        "provider_id": key or "unknown",
        "free_tier": True,
        "realtime_capable": False,
        "supports_ohlcv": False,
        "supports_quote": False,
        "supports_websocket": False,
        "max_requests_per_minute": None,
        "max_requests_per_day": None,
    }


def _normalize_provider_id(provider_id: Optional[str]) -> str:
    raw = str(provider_id or "").strip().lower()
    if raw.startswith("yahoo"):
        return "yahoo"
    return raw


def looks_like_quota_error(message: Any) -> bool:
    text = str(message or "").lower()
    return any(marker in text for marker in QUOTA_MARKERS)


def classify_quote_status(
    ticker: Optional[Dict[str, Any]],
    provider_id: Optional[str] = None,
    *,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Classify a received quote. Never trust the provider name alone."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    caps = capabilities_for(provider_id or (ticker or {}).get("source"))
    if not ticker:
        return {
            "status": "DATA_UNAVAILABLE",
            "tradable": False,
            "realtime": False,
            "data_age_ms": None,
            "reason": "DATA_UNAVAILABLE",
            "capabilities": caps,
        }

    declared = str(ticker.get("status") or "").upper()
    source = str(ticker.get("source") or provider_id or "").lower()
    age_ms = None
    timestamp = ticker.get("timestamp")
    try:
        if timestamp is not None:
            age_ms = max(0, now - int(timestamp))
    except (TypeError, ValueError):
        age_ms = None

    if declared in {"ERROR", "OFFLINE"}:
        status = declared
    elif looks_like_quota_error(ticker.get("reason") or ticker.get("error")):
        status = "ERROR"
        declared = "PROVIDER_QUOTA_EXCEEDED"
    elif declared == "STALE":
        # v3.3.2 (D3): a quote restored from the persisted last-good cache is
        # REAL data — but cached. It is ALWAYS presented as STALE, never
        # downgraded to LIVE/DELAYED by the age fallback below (a cached
        # quote younger than 15 min used to slip through as LIVE).
        status = "STALE"
    elif "yahoo" in source or declared == "DELAYED":
        status = "DELAYED"
    elif declared == "LIVE":
        # Trust a LIVE payload from a realtime-capable feed. Age is still
        # exposed so the execution freshness gate can refuse a stale tick.
        status = "LIVE"
    elif not caps.get("realtime_capable"):
        status = "DELAYED"
    elif age_ms is not None and age_ms > 15 * 60 * 1000:
        status = "STALE"
    elif ticker.get("last"):
        status = "LIVE" if caps.get("realtime_capable") else "DELAYED"
    else:
        status = "DATA_UNAVAILABLE"

    return {
        "status": status,
        "tradable": status == "LIVE",
        "realtime": status == "LIVE",
        "data_age_ms": age_ms,
        "reason": declared if declared == "PROVIDER_QUOTA_EXCEEDED" else status,
        "capabilities": caps,
    }
