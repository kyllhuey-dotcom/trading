from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import pandas as pd
from datetime import datetime

class MarketData:
    def __init__(self, symbol: str, asset_class: str, last: float, timestamp: int, source: str):
        self.symbol = symbol
        self.asset_class = asset_class
        self.last = last
        self.timestamp = timestamp
        self.source = source
        self.bid: Optional[float] = None
        self.ask: Optional[float] = None
        self.open: Optional[float] = None
        self.high: Optional[float] = None
        self.low: Optional[float] = None
        self.close: Optional[float] = None
        self.volume: Optional[float] = None
        self.spread: Optional[float] = None
        self.status: str = "LIVE"
        self.latency_ms: int = 0

    def to_dict(self):
        return self.__dict__

class BaseDataProvider(ABC):
    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Optional[MarketData]:
        pass

    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        pass
