import ccxt.async_support as ccxt
import pandas as pd
import logging
from typing import List, Optional, Dict, Any
from .base_provider import MarketDataProvider, TickerModel
from datetime import datetime

logger = logging.getLogger("BinanceProvider")


class BinanceProvider(MarketDataProvider):
    """
    Crypto Data Provider using Binance Public API (fallback #3 in the chain).
    """

    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
        })
        self.source_name = "Binance"
        self._last_health_check = {}

    async def get_symbols(self) -> List[str]:
        try:
            markets = await self.exchange.load_markets()
            return [symbol for symbol in markets if '/USDT' in symbol]
        except Exception as e:
            logger.warning(f"Binance discovery error: {e}")
            return []

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return TickerModel(
                symbol=symbol,
                name=symbol,
                asset_class="CRYPTO",
                exchange="Binance",
                timestamp=ticker.get('timestamp') or int(datetime.now().timestamp() * 1000),
                bid=ticker.get('bid'),
                ask=ticker.get('ask'),
                last=ticker['last'],
                spread=(ticker['ask'] - ticker['bid']) if ticker.get('ask') and ticker.get('bid') else None,
                volume=ticker.get('baseVolume'),
                source=self.source_name,
                status="LIVE"
            )
        except Exception as e:
            logger.debug(f"Binance quote error ({symbol}): {e}")
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            return df
        except Exception as e:
            logger.debug(f"Binance OHLCV error ({symbol}): {e}")
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
        try:
            start = datetime.now()
            await self.exchange.fetch_ticker('BTC/USDT')
            latency = (datetime.now() - start).total_seconds() * 1000
            self._last_health_check = {
                "provider": self.source_name,
                "status": "ONLINE",
                "latency_ms": int(latency),
                "last_update": datetime.now().isoformat()
            }
        except Exception as e:
            self._last_health_check = {
                "provider": self.source_name,
                "status": "ERROR",
                "message": str(e),
                "last_update": datetime.now().isoformat()
            }
        return self._last_health_check

    async def close(self):
        await self.exchange.close()
