"""Reusable keyless CCXT market-data provider."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .base_provider import MarketDataProvider, TickerModel

logger = logging.getLogger(__name__)


class PublicCCXTProvider(MarketDataProvider):
    def __init__(self, exchange: Any, source_name: str):
        self.exchange = exchange
        self.source_name = source_name

    async def get_symbols(self) -> List[str]:
        try:
            markets = await self.exchange.load_markets()
            return [symbol for symbol in markets if "/USDT" in symbol or "/USD" in symbol]
        except Exception as exc:
            logger.debug("%s discovery error: %s", self.source_name, exc)
            return []

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            last = ticker.get("last") or ticker.get("close")
            if last is None:
                return None
            bid, ask = ticker.get("bid"), ticker.get("ask")
            return TickerModel(
                symbol=symbol,
                name=symbol,
                asset_class="CRYPTO",
                exchange=self.source_name,
                timestamp=ticker.get("timestamp") or int(datetime.now().timestamp() * 1000),
                bid=bid,
                ask=ask,
                last=float(last),
                spread=(float(ask) - float(bid)) if ask is not None and bid is not None else None,
                volume=ticker.get("baseVolume") or ticker.get("quoteVolume"),
                change_24h=ticker.get("percentage"),
                source=self.source_name,
                status="LIVE",
            )
        except Exception as exc:
            logger.debug("%s quote error (%s): %s", self.source_name, symbol, exc)
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                        limit: int = 100) -> pd.DataFrame:
        try:
            rows = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return pd.DataFrame(
                rows, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"]
            )
        except Exception as exc:
            logger.debug("%s OHLCV error (%s): %s", self.source_name, symbol, exc)
            return pd.DataFrame()

    async def get_order_book(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.exchange.fetch_order_book(symbol, limit=20)
        except Exception:
            return None

    async def get_recent_trades(self, symbol: str) -> Optional[List[Dict[str, Any]]]:
        try:
            return await self.exchange.fetch_trades(symbol, limit=50)
        except Exception:
            return None

    async def health_check(self) -> Dict[str, Any]:
        start = datetime.now()
        try:
            quote = await self.get_quote("BTC/USDT")
            if quote is None:
                quote = await self.get_quote("BTC/USD")
            latency = (datetime.now() - start).total_seconds() * 1000
            return {"provider": self.source_name,
                    "status": "ONLINE" if quote else "ERROR",
                    "latency_ms": int(latency)}
        except Exception as exc:
            return {"provider": self.source_name, "status": "ERROR", "message": str(exc)}

    async def close(self) -> None:
        await self.exchange.close()
