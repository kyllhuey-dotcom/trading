"""Native public REST fallbacks for Kraken and OKX (no API key).

CCXT remains the primary path. These helpers are used only when CCXT returns
no quote/OHLCV so a single library glitch cannot wipe the live crypto feed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .base_provider import TickerModel

KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"

_KRAKEN_INTERVAL = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}
_OKX_BAR = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "4h": "4H", "1d": "1D",
}


def ccxt_to_okx_inst(symbol: str) -> str:
    return str(symbol or "").replace("/", "-").upper()


def ccxt_to_kraken_pair(symbol: str) -> str:
    raw = str(symbol or "").upper().replace("-", "/")
    if "/" not in raw:
        return raw.replace("BTC", "XBT")
    base, quote = raw.split("/", 1)
    if base == "BTC":
        base = "XBT"
    return f"{base}{quote}"


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def parse_kraken_ticker(payload: Any, symbol: str) -> Optional[TickerModel]:
    if not isinstance(payload, dict) or payload.get("error"):
        errors = payload.get("error") if isinstance(payload, dict) else None
        if errors:
            return None
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or not result:
        return None
    row = next(iter(result.values()))
    if not isinstance(row, dict):
        return None
    last_raw = row.get("c") or row.get("last")
    last = last_raw[0] if isinstance(last_raw, (list, tuple)) and last_raw else last_raw
    try:
        last_f = float(last)
    except (TypeError, ValueError):
        return None
    bid = _first_float(row.get("b"))
    ask = _first_float(row.get("a"))
    volume = _first_float(row.get("v"), index=-1)
    change = None
    try:
        open_px = float(row.get("o"))
        if open_px > 0:
            change = (last_f - open_px) / open_px * 100.0
    except (TypeError, ValueError):
        change = None
    return TickerModel(
        symbol=symbol,
        name=symbol,
        asset_class="CRYPTO",
        exchange="Kraken",
        timestamp=_now_ms(),
        bid=bid,
        ask=ask,
        last=last_f,
        spread=(ask - bid) if ask is not None and bid is not None else None,
        volume=volume,
        change_24h=change,
        source="Kraken",
        status="LIVE",
    )


def parse_okx_ticker(payload: Any, symbol: str) -> Optional[TickerModel]:
    if not isinstance(payload, dict) or str(payload.get("code", "0")) not in ("0", "OK"):
        if isinstance(payload, dict) and payload.get("code") not in (None, "0", 0, "OK"):
            return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    if not isinstance(row, dict):
        return None
    try:
        last_f = float(row.get("last") or row.get("lastPx"))
    except (TypeError, ValueError):
        return None
    bid = _maybe_float(row.get("bidPx"))
    ask = _maybe_float(row.get("askPx"))
    ts = row.get("ts")
    try:
        timestamp = int(ts) if ts is not None else _now_ms()
    except (TypeError, ValueError):
        timestamp = _now_ms()
    return TickerModel(
        symbol=symbol,
        name=symbol,
        asset_class="CRYPTO",
        exchange="OKX",
        timestamp=timestamp,
        bid=bid,
        ask=ask,
        last=last_f,
        spread=(ask - bid) if ask is not None and bid is not None else None,
        volume=_maybe_float(row.get("vol24h") or row.get("volCcy24h")),
        change_24h=_maybe_float(row.get("sodUtc8")),
        source="OKX",
        status="LIVE",
    )


def parse_kraken_ohlc(payload: Any) -> pd.DataFrame:
    if not isinstance(payload, dict):
        return pd.DataFrame()
    result = payload.get("result")
    if not isinstance(result, dict):
        return pd.DataFrame()
    rows = None
    for key, value in result.items():
        if key == "last":
            continue
        if isinstance(value, list):
            rows = value
            break
    return _rows_to_ohlcv(rows or [])


def parse_okx_candles(payload: Any) -> pd.DataFrame:
    if not isinstance(payload, dict):
        return pd.DataFrame()
    data = payload.get("data")
    if not isinstance(data, list):
        return pd.DataFrame()
    # OKX returns newest first.
    ordered = list(reversed(data))
    return _rows_to_ohlcv(ordered)


def _rows_to_ohlcv(rows: List[Any]) -> pd.DataFrame:
    parsed: List[List[float]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            ts = float(row[0])
            # Kraken uses seconds; OKX uses ms.
            if ts < 10_000_000_000:
                ts *= 1000.0
            parsed.append([
                ts,
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]) if len(row) > 5 else 0.0,
            ])
        except (TypeError, ValueError):
            continue
    if not parsed:
        return pd.DataFrame()
    return pd.DataFrame(
        parsed, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    )


def _first_float(value: Any, index: int = 0) -> Optional[float]:
    if isinstance(value, (list, tuple)) and value:
        pick = value[index] if abs(index) < len(value) else value[0]
        return _maybe_float(pick)
    return _maybe_float(value)


def _maybe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def kraken_ticker_params(symbol: str) -> Dict[str, str]:
    return {"pair": ccxt_to_kraken_pair(symbol)}


def kraken_ohlc_params(symbol: str, timeframe: str) -> Dict[str, Any]:
    return {
        "pair": ccxt_to_kraken_pair(symbol),
        "interval": _KRAKEN_INTERVAL.get(timeframe, 1),
    }


def okx_ticker_params(symbol: str) -> Dict[str, str]:
    return {"instId": ccxt_to_okx_inst(symbol)}


def okx_candle_params(symbol: str, timeframe: str, limit: int) -> Dict[str, Any]:
    return {
        "instId": ccxt_to_okx_inst(symbol),
        "bar": _OKX_BAR.get(timeframe, "1m"),
        "limit": str(max(1, min(int(limit or 100), 300))),
    }


def rest_urls(kind: str) -> Tuple[str, str]:
    if kind == "kraken":
        return KRAKEN_TICKER_URL, KRAKEN_OHLC_URL
    return OKX_TICKER_URL, OKX_CANDLES_URL
