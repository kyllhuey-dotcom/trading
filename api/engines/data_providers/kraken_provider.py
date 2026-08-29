"""Kraken public market data: CCXT first, native REST fallback."""
from __future__ import annotations

from typing import Optional

import ccxt.async_support as ccxt
import httpx
import pandas as pd

from .base_provider import TickerModel
from .exchange_rest import (
    KRAKEN_OHLC_URL,
    KRAKEN_TICKER_URL,
    kraken_ohlc_params,
    kraken_ticker_params,
    parse_kraken_ohlc,
    parse_kraken_ticker,
)
from .public_ccxt_provider import PublicCCXTProvider


class KrakenProvider(PublicCCXTProvider):
    def __init__(self):
        super().__init__(ccxt.kraken({"enableRateLimit": True}), "Kraken")

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        quote = await super().get_quote(symbol)
        if quote is not None:
            return quote
        return await self._rest_quote(symbol)

    async def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                        limit: int = 100) -> pd.DataFrame:
        frame = await super().get_ohlcv(symbol, timeframe, limit)
        if frame is not None and not frame.empty:
            return frame
        return await self._rest_ohlcv(symbol, timeframe, limit)

    async def _rest_quote(self, symbol: str) -> Optional[TickerModel]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    KRAKEN_TICKER_URL, params=kraken_ticker_params(symbol))
            if response.status_code != 200:
                return None
            return parse_kraken_ticker(response.json(), symbol)
        except Exception:
            return None

    async def _rest_ohlcv(self, symbol: str, timeframe: str,
                          limit: int) -> pd.DataFrame:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    KRAKEN_OHLC_URL, params=kraken_ohlc_params(symbol, timeframe))
            if response.status_code != 200:
                return pd.DataFrame()
            frame = parse_kraken_ohlc(response.json())
            if limit and len(frame) > limit:
                return frame.tail(int(limit)).reset_index(drop=True)
            return frame
        except Exception:
            return pd.DataFrame()
