"""Batched, cached Yahoo Finance fallback for delayed tradfi data."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

from .base_provider import MarketDataProvider, TickerModel

logger = logging.getLogger(__name__)


class YahooProvider(MarketDataProvider):
    """Yahoo fallback with one grouped download per asset-class scan cycle.

    Yahoo's free feed is delayed and is never presented as realtime.  A 1-minute
    grouped download supplies both quotes and OHLCV; 15-minute candles are
    derived locally.  This replaces the previous ~15 requests per ticker.
    """

    TICKER_TTL_S = 60.0
    OHLCV_TTL_S = {"1m": 60.0, "15m": 300.0}

    def __init__(self, asset_class: str):
        self.asset_class = asset_class
        self.source_name = "Yahoo Finance"
        self._ticker_cache: Dict[str, tuple[float, TickerModel]] = {}
        self._ohlcv_cache: Dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self._batch_lock = asyncio.Lock()
        self._failure_state: Dict[str, tuple[int, float]] = {}
        self.batch_calls = 0

    async def get_symbols(self) -> List[str]:
        return []

    def _cached_quote(self, symbol: str) -> Optional[TickerModel]:
        cached = self._ticker_cache.get(symbol)
        if cached and time.time() - cached[0] < self.TICKER_TTL_S:
            return cached[1]
        return None

    def _cached_ohlcv(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        cached = self._ohlcv_cache.get((symbol, timeframe))
        ttl = self.OHLCV_TTL_S.get(timeframe, 60.0)
        if cached and time.time() - cached[0] < ttl:
            return cached[1].copy()
        return None

    def _in_cooldown(self, symbol: str) -> bool:
        count, failed_at = self._failure_state.get(symbol, (0, 0.0))
        cooldown = min(900.0, 30.0 * (2 ** max(0, count - 1)))
        return bool(count and time.time() - failed_at < cooldown)

    def _record_failure(self, symbol: str) -> None:
        count, _ = self._failure_state.get(symbol, (0, 0.0))
        self._failure_state[symbol] = (count + 1, time.time())

    def _record_success(self, symbol: str) -> None:
        self._failure_state.pop(symbol, None)

    @staticmethod
    def _normalize_history(data: pd.DataFrame) -> pd.DataFrame:
        if data is None or data.empty:
            return pd.DataFrame()
        frame = data.copy().dropna(subset=["Close"] if "Close" in data.columns else None)
        if frame.empty:
            return pd.DataFrame()
        frame = frame.reset_index()
        first = frame.columns[0]
        frame = frame.rename(columns={first: "Timestamp"})
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(column in frame.columns for column in required):
            return pd.DataFrame()

        def to_epoch_ms(value: Any) -> int:
            if isinstance(value, (int, float)):
                number = int(value)
                return number if number > 10_000_000_000 else number * 1000
            return int(pd.Timestamp(value).timestamp() * 1000)

        frame["Timestamp"] = frame["Timestamp"].apply(to_epoch_ms)
        for column in required:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
        return frame[["Timestamp", *required]].dropna(subset=["Close"])

    @staticmethod
    def _symbol_frame(download: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if download is None or download.empty:
            return pd.DataFrame()
        if not isinstance(download.columns, pd.MultiIndex):
            return download
        level0 = download.columns.get_level_values(0)
        level1 = download.columns.get_level_values(1)
        try:
            if symbol in level0:
                return download[symbol]
            if symbol in level1:
                return download.xs(symbol, axis=1, level=1)
        except (KeyError, ValueError):
            return pd.DataFrame()
        return pd.DataFrame()

    @staticmethod
    def _resample_15m(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        work = frame.copy()
        work["_bucket"] = range(len(work))
        work["_bucket"] = work["_bucket"] // 15
        grouped = work.groupby("_bucket", sort=True).agg({
            "Timestamp": "first", "Open": "first", "High": "max",
            "Low": "min", "Close": "last", "Volume": "sum",
        })
        return grouped.reset_index(drop=True)

    def _cache_frame(self, symbol: str, frame: pd.DataFrame) -> None:
        if frame.empty:
            self._record_failure(symbol)
            return
        now = time.time()
        self._record_success(symbol)
        self._ohlcv_cache[(symbol, "1m")] = (now, frame.copy())
        self._ohlcv_cache[(symbol, "15m")] = (now, self._resample_15m(frame))
        last_row = frame.iloc[-1]
        first_row = frame.iloc[0]
        last_price = float(last_row["Close"])
        open_price = float(first_row["Open"])
        change_pct = ((last_price - open_price) / open_price * 100) if open_price else 0.0
        self._ticker_cache[symbol] = (now, TickerModel(
            symbol=symbol,
            name=symbol,
            asset_class=self.asset_class,
            exchange="Global Market",
            timestamp=int(last_row["Timestamp"]),
            last=last_price,
            change_24h=change_pct,
            open=float(last_row["Open"]),
            high=float(last_row["High"]),
            low=float(last_row["Low"]),
            volume=float(last_row["Volume"]),
            source=self.source_name,
            status="DELAYED",
        ))

    async def prepare_cycle(self, symbols: List[str]) -> None:
        """Prime a whole asset class with at most one ``yf.download`` call."""
        unique = list(dict.fromkeys(symbol for symbol in symbols if symbol))
        missing = [symbol for symbol in unique
                   if self._cached_quote(symbol) is None and not self._in_cooldown(symbol)]
        if not missing:
            return
        async with self._batch_lock:
            missing = [symbol for symbol in missing if self._cached_quote(symbol) is None]
            if not missing or not hasattr(yf, "download"):
                return
            try:
                self.batch_calls += 1
                downloaded = await asyncio.to_thread(
                    yf.download,
                    tickers=missing,
                    period="5d",
                    interval="1m",
                    group_by="ticker",
                    threads=False,
                    progress=False,
                    auto_adjust=False,
                )
                for symbol in missing:
                    frame = self._normalize_history(self._symbol_frame(downloaded, symbol))
                    self._cache_frame(symbol, frame)
            except Exception as exc:
                logger.debug("Yahoo batch failed (%s): %s", self.asset_class, exc)
                for symbol in missing:
                    self._record_failure(symbol)

    async def _single_history(self, symbol: str, interval: str = "1m") -> pd.DataFrame:
        """Compatibility fallback for isolated calls and mocked legacy clients."""
        try:
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            data = await asyncio.to_thread(
                ticker.history, period="5d" if interval != "1m" else "1d", interval=interval
            )
            return self._normalize_history(data)
        except Exception as exc:
            logger.debug("Yahoo history error (%s): %s", symbol, exc)
            return pd.DataFrame()

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        cached = self._cached_quote(symbol)
        if cached:
            return cached
        if self._in_cooldown(symbol):
            return None
        await self.prepare_cycle([symbol])
        cached = self._cached_quote(symbol)
        if cached:
            return cached

        # Used only when yfinance has no grouped API (notably test fixtures) or
        # a direct one-off call occurs outside a scanner cycle.
        frame = await self._single_history(symbol, "1m")
        if frame.empty:
            frame = await self._single_history(symbol, "1d")
        self._cache_frame(symbol, frame)
        return self._cached_quote(symbol)

    async def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                        limit: int = 100) -> pd.DataFrame:
        timeframe = timeframe if timeframe in ("1m", "15m") else timeframe
        cached = self._cached_ohlcv(symbol, timeframe)
        if cached is not None:
            return cached.tail(limit)
        if self._in_cooldown(symbol):
            return pd.DataFrame()
        await self.prepare_cycle([symbol])
        cached = self._cached_ohlcv(symbol, timeframe)
        if cached is not None:
            return cached.tail(limit)

        interval = timeframe if timeframe in ("1m", "5m", "15m", "1h", "1d") else "1m"
        frame = await self._single_history(symbol, interval)
        if interval == "1m":
            self._cache_frame(symbol, frame)
            cached = self._cached_ohlcv(symbol, timeframe)
            return cached.tail(limit) if cached is not None else pd.DataFrame()
        if not frame.empty:
            self._ohlcv_cache[(symbol, timeframe)] = (time.time(), frame.copy())
            self._record_success(symbol)
        return frame.tail(limit)

    async def health_check(self) -> Dict[str, Any]:
        if any(self._cached_quote(symbol) for symbol in list(self._ticker_cache)):
            return {"provider": f"Yahoo_{self.asset_class}", "status": "ONLINE",
                    "cached": True, "batch_calls": self.batch_calls}
        quote = await self.get_quote("EURUSD=X")
        return {
            "provider": f"Yahoo_{self.asset_class}",
            "status": "ONLINE" if quote else "ERROR",
            "batch_calls": self.batch_calls,
        }
