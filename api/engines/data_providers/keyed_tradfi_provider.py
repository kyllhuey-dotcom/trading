"""Optional API-key tradfi providers with provider-scoped rate limiting."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

from .base_provider import MarketDataProvider, TickerModel


class ProviderRateLimiter:
    """Small monotonic limiter; each provider owns one independent instance."""

    def __init__(self, requests_per_minute: int):
        self.min_interval_s = 60.0 / max(1, requests_per_minute)
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self.min_interval_s - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class TwelveDataProvider(MarketDataProvider):
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str, requests_per_minute: int = 8):
        if not api_key:
            raise ValueError("TWELVEDATA_API_KEY is required")
        self.api_key = api_key
        self.source_name = "TwelveData"
        self.rate_limiter = ProviderRateLimiter(requests_per_minute)

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.rate_limiter.wait()
        query = {**params, "apikey": self.api_key}
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.BASE_URL}/{path}", params=query, timeout=10.0)
            response.raise_for_status()
            return response.json()

    async def get_symbols(self) -> List[str]:
        return []

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        try:
            data = await self._get("quote", {"symbol": symbol})
            last = data.get("close") or data.get("price")
            if last is None or data.get("status") == "error":
                return None
            timestamp = int(time.time() * 1000)
            if data.get("timestamp"):
                timestamp = int(float(data["timestamp"]) * 1000)
            return TickerModel(
                symbol=symbol, name=data.get("name") or symbol,
                asset_class="TRADFI", exchange=data.get("exchange") or "TwelveData",
                timestamp=timestamp, last=float(last),
                open=_float_or_none(data.get("open")), high=_float_or_none(data.get("high")),
                low=_float_or_none(data.get("low")), volume=_float_or_none(data.get("volume")),
                change_24h=_float_or_none(data.get("percent_change")),
                source=self.source_name, status="LIVE",
            )
        except Exception:
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                        limit: int = 100) -> pd.DataFrame:
        interval = {"1m": "1min", "5m": "5min", "15m": "15min",
                    "1h": "1h", "1d": "1day"}.get(timeframe, "1min")
        try:
            data = await self._get("time_series", {
                "symbol": symbol, "interval": interval, "outputsize": limit,
            })
            values = data.get("values") or []
            rows = []
            for candle in reversed(values):
                ts = int(pd.Timestamp(candle["datetime"], tz="UTC").timestamp() * 1000)
                rows.append([ts, float(candle["open"]), float(candle["high"]),
                             float(candle["low"]), float(candle["close"]),
                             float(candle.get("volume") or 0)])
            return pd.DataFrame(rows, columns=[
                "Timestamp", "Open", "High", "Low", "Close", "Volume",
            ])
        except Exception:
            return pd.DataFrame()

    async def health_check(self) -> Dict[str, Any]:
        start = time.monotonic()
        quote = await self.get_quote("AAPL")
        return {"provider": self.source_name, "status": "ONLINE" if quote else "ERROR",
                "latency_ms": int((time.monotonic() - start) * 1000)}


class FinnhubProvider(MarketDataProvider):
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, requests_per_minute: int = 30):
        if not api_key:
            raise ValueError("FINNHUB_API_KEY is required")
        self.api_key = api_key
        self.source_name = "Finnhub"
        self.rate_limiter = ProviderRateLimiter(requests_per_minute)

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.rate_limiter.wait()
        query = {**params, "token": self.api_key}
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.BASE_URL}/{path}", params=query, timeout=10.0)
            response.raise_for_status()
            return response.json()

    async def get_symbols(self) -> List[str]:
        return []

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        try:
            data = await self._get("quote", {"symbol": symbol})
            last = data.get("c")
            if last in (None, 0, 0.0):
                return None
            return TickerModel(
                symbol=symbol, name=symbol, asset_class="TRADFI", exchange="Finnhub",
                timestamp=int(float(data.get("t") or time.time()) * 1000),
                last=float(last), open=_float_or_none(data.get("o")),
                high=_float_or_none(data.get("h")), low=_float_or_none(data.get("l")),
                change_24h=_float_or_none(data.get("dp")),
                source=self.source_name, status="LIVE",
            )
        except Exception:
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                        limit: int = 100) -> pd.DataFrame:
        resolution = {"1m": "1", "5m": "5", "15m": "15",
                      "1h": "60", "1d": "D"}.get(timeframe, "1")
        seconds = max(86400, limit * (60 if resolution == "1" else 900))
        now = int(time.time())
        try:
            data = await self._get("stock/candle", {
                "symbol": symbol, "resolution": resolution,
                "from": now - seconds, "to": now,
            })
            if data.get("s") != "ok":
                return pd.DataFrame()
            rows = list(zip(data.get("t", []), data.get("o", []), data.get("h", []),
                            data.get("l", []), data.get("c", []), data.get("v", [])))
            return pd.DataFrame(
                [[int(row[0]) * 1000, *map(float, row[1:])] for row in rows[-limit:]],
                columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"],
            )
        except Exception:
            return pd.DataFrame()

    async def health_check(self) -> Dict[str, Any]:
        start = datetime.now()
        quote = await self.get_quote("AAPL")
        return {"provider": self.source_name, "status": "ONLINE" if quote else "ERROR",
                "latency_ms": int((datetime.now() - start).total_seconds() * 1000)}


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
