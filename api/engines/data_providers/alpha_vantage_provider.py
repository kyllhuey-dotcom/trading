"""Alpha Vantage provider — free-tier realtime tradfi data (v2.8).

Purpose
-------
Crypto providers (Binance, Bybit, OKX, Kraken, Coinbase, Gate) are already
realtime. Yahoo Finance is the delayed, keyless last resort for tradfi
(stocks, indices, forex). Alpha Vantage <https://www.alphavantage.co/> offers
a free API key (25 requests/day, 5 requests/minute) with near-realtime US
equity and forex data — a strictly better source than delayed Yahoo quotes
for the periodic scan cycle this bot runs.

Design
------
- `ALPHA_VANTAGE_API_KEY` activates the provider (optional). When the key is
  missing the provider is simply not registered and the cascade falls back
  to Yahoo Finance unchanged (fail-safe, never blocking).
- One shared `ProviderRateLimiter` enforces the 5-calls/minute limit.
- A UTC-day call counter enforces the 25-calls/day free quota client-side:
  once exhausted the provider refuses every call and the data-layer cascade
  transparently falls back to Yahoo (no exceptions leak into the scan).
- Alpha Vantage answers quota breaches with HTTP 200 + a JSON `Note` /
  `Information` payload — those are detected and treated as quota exhaustion
  so the fallback engages immediately.

Symbol conventions
------------------
The engine passes Yahoo-style symbols. Forex pairs (`EURUSD=X`, `EUR/USD`)
are routed to the FX endpoints; plain tickers (`AAPL`, `MSFT`) go to the
equity endpoints. Indices / commodities / futures / bonds are not covered by
the free tier and stay on Yahoo by wiring decision in `data_engine`.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd

from .base_provider import MarketDataProvider, TickerModel
from .keyed_tradfi_provider import ProviderRateLimiter


class _QuotaExceeded(Exception):
    """Internal signal: free-tier quota exhausted (day limit or API note)."""


class AlphaVantageProvider(MarketDataProvider):
    """Free-tier Alpha Vantage feed for US equities and forex pairs."""

    BASE_URL = "https://www.alphavantage.co/query"

    # Free tier: 5 requests/minute, 25 requests/day.
    def __init__(self, api_key: str, requests_per_minute: int = 5,
                 daily_quota: int = 25):
        if not api_key:
            raise ValueError("ALPHA_VANTAGE_API_KEY is required")
        self.api_key = api_key
        self.source_name = "AlphaVantage"
        self.rate_limiter = ProviderRateLimiter(requests_per_minute)
        self.daily_quota = max(1, int(daily_quota))
        self._usage_day: str = self._utc_day()
        self._usage_count: int = 0

    # ------------------------------------------------------------------ #
    # Quota bookkeeping                                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _utc_day() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _rollover_day(self) -> None:
        today = self._utc_day()
        if today != self._usage_day:
            self._usage_day = today
            self._usage_count = 0

    @property
    def daily_calls_remaining(self) -> int:
        self._rollover_day()
        return max(0, self.daily_quota - self._usage_count)

    def _consume_call(self) -> None:
        self._rollover_day()
        self._usage_count += 1

    # ------------------------------------------------------------------ #
    # HTTP                                                                  #
    # ------------------------------------------------------------------ #
    async def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.daily_calls_remaining <= 0:
            raise _QuotaExceeded("Alpha Vantage free daily quota exhausted")
        await self.rate_limiter.wait()
        self._consume_call()
        query = {**params, "apikey": self.api_key}
        async with httpx.AsyncClient() as client:
            response = await client.get(self.BASE_URL, params=query, timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            return {}
        # Alpha Vantage signals quota breaches with a 200 + Note/Information.
        note = str(payload.get("Note") or payload.get("Information") or "")
        if note:
            # Burn the remaining allowance locally so the fallback engages
            # deterministically for the rest of the day.
            self._usage_count = self.daily_quota
            raise _QuotaExceeded(note)
        return payload

    # ------------------------------------------------------------------ #
    # Symbol helpers                                                        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def forex_pair(symbol: str) -> Optional[Tuple[str, str]]:
        """'EURUSD=X' or 'EUR/USD' -> ('EUR', 'USD'); None for non-forex."""
        cleaned = symbol.strip().upper()
        if cleaned.endswith("=X") and len(cleaned) == 8:
            return cleaned[:3], cleaned[3:6]
        if "/" in cleaned:
            left, right = cleaned.split("/", 1)
            if len(left) == 3 and len(right) >= 3:
                return left, right[:3]
        return None

    async def get_symbols(self) -> List[str]:
        return []

    # ------------------------------------------------------------------ #
    # Quotes                                                                #
    # ------------------------------------------------------------------ #
    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        pair = self.forex_pair(symbol)
        try:
            if pair:
                return await self._forex_quote(symbol, pair)
            return await self._equity_quote(symbol)
        except _QuotaExceeded:
            return None
        except Exception:
            return None

    async def _forex_quote(self, symbol: str, pair: Tuple[str, str]) -> Optional[TickerModel]:
        data = await self._get({
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": pair[0],
            "to_currency": pair[1],
        })
        block = data.get("Realtime Currency Exchange Rate") or {}
        rate = block.get("5. Exchange Rate")
        if rate in (None, ""):
            return None
        bid = _float_or_none(block.get("8. Bid Price"))
        ask = _float_or_none(block.get("9. Ask Price"))
        last = float(rate)
        spread = (ask - bid) if (bid and ask and ask >= bid) else None
        return TickerModel(
            symbol=symbol, name=f"{pair[0]}/{pair[1]}", asset_class="FOREX",
            exchange="AlphaVantage FX", timestamp=int(time.time() * 1000),
            bid=bid, ask=ask, last=last,
            spread=spread, source=self.source_name, status="LIVE",
        )

    async def _equity_quote(self, symbol: str) -> Optional[TickerModel]:
        data = await self._get({"function": "GLOBAL_QUOTE", "symbol": symbol})
        block = data.get("Global Quote") or {}
        last = block.get("05. price")
        if last in (None, ""):
            return None
        change_pct = str(block.get("10. change percent", "")).rstrip("%")
        return TickerModel(
            symbol=symbol, name=symbol, asset_class="STOCKS",
            exchange="AlphaVantage", timestamp=int(time.time() * 1000),
            last=float(last),
            open=_float_or_none(block.get("02. open")),
            high=_float_or_none(block.get("03. high")),
            low=_float_or_none(block.get("04. low")),
            volume=_float_or_none(block.get("06. volume")),
            change_24h=_float_or_none(change_pct),
            source=self.source_name, status="LIVE",
        )

    # ------------------------------------------------------------------ #
    # OHLCV                                                                 #
    # ------------------------------------------------------------------ #
    async def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                        limit: int = 100) -> pd.DataFrame:
        interval = {"1m": "1min", "5m": "5min", "15m": "15min"}.get(timeframe, "1min")
        pair = self.forex_pair(symbol)
        try:
            if pair:
                data = await self._get({
                    "function": "FX_INTRADAY", "from_symbol": pair[0],
                    "to_symbol": pair[1], "interval": interval, "outputsize": "compact",
                })
                series = data.get(f"Time Series FX ({interval})") or {}
                has_volume = False
            else:
                data = await self._get({
                    "function": "TIME_SERIES_INTRADAY", "symbol": symbol,
                    "interval": interval, "outputsize": "compact",
                })
                series = data.get(f"Time Series ({interval})") or {}
                has_volume = True
        except _QuotaExceeded:
            return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

        rows = []
        # Alpha Vantage returns most-recent-first — walk in date order.
        for stamp, candle in sorted(series.items()):
            try:
                ts = int(pd.Timestamp(stamp, tz="UTC").timestamp() * 1000)
                volume = float(candle.get("5. volume", 0) or 0) if has_volume else 0.0
                rows.append([
                    ts,
                    float(candle["1. open"]),
                    float(candle["2. high"]),
                    float(candle["3. low"]),
                    float(candle["4. close"]),
                    volume,
                ])
            except (KeyError, TypeError, ValueError):
                continue
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows, columns=[
            "Timestamp", "Open", "High", "Low", "Close", "Volume",
        ])
        return frame.tail(limit).reset_index(drop=True)

    async def health_check(self) -> Dict[str, Any]:
        start = time.monotonic()
        quote = await self.get_quote("AAPL")
        return {
            "provider": self.source_name,
            "status": "ONLINE" if quote else "ERROR",
            "latency_ms": int((time.monotonic() - start) * 1000),
            "daily_calls_remaining": self.daily_calls_remaining,
        }


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
