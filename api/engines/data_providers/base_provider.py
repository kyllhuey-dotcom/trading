from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import pandas as pd
from pydantic import BaseModel

class TickerModel(BaseModel):
    symbol: str
    name: Optional[str] = None
    asset_class: str
    exchange: str
    timestamp: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    change_24h: Optional[float] = None
    spread: Optional[float] = None
    volume: Optional[float] = None
    source: str
    status: str # LIVE, DELAYED, STALE, OFFLINE, ERROR

class OHLCVModel(BaseModel):
    symbol: str
    timeframe: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str

class MarketDataProvider(ABC):
    @abstractmethod
    async def get_symbols(self) -> List[str]:
        """Discovery of available symbols from this provider."""
        pass

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        """Get latest price/ticker for a specific symbol."""
        pass

    @abstractmethod
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """Get historical candles."""
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Return provider status, latency and last update."""
        pass

    async def get_order_book(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Optional: Get current order book."""
        return None

    async def get_recent_trades(self, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """Optional: Get recent trade history."""
        return None
