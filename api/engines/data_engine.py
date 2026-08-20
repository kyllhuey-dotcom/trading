import asyncio
import pandas as pd
from typing import Dict, Any, List, Optional
from datetime import datetime

from .data_providers.crypto_provider import CryptoProvider
from .data_providers.yahoo_provider import YahooProvider
from .market_catalog import MarketCatalog

class DataEngine:
    def __init__(self):
        self.crypto_provider = CryptoProvider()
        self.yahoo_forex = YahooProvider("FOREX")
        self.yahoo_commodity = YahooProvider("COMMODITY")
        self.yahoo_index = YahooProvider("INDEX")
        self.catalog = MarketCatalog()

    def _get_provider(self, symbol: str):
        info = self.catalog.get_info(symbol)
        if info.get("provider") == "crypto":
            return self.crypto_provider
        elif info.get("class") == "FOREX":
            return self.yahoo_forex
        elif info.get("class") == "COMMODITY":
            return self.yahoo_commodity
        elif info.get("class") == "INDEX":
            return self.yahoo_index
        return None

    async def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        provider = self._get_provider(symbol)
        if not provider:
            return None
        
        md = await provider.fetch_ticker(symbol)
        if md:
            # Fraîcheur check (Rule 5)
            now_ms = int(datetime.now().timestamp() * 1000)
            md.data_age_ms = now_ms - md.timestamp
            
            # Threshold varies by asset class
            limit = 60000 # 1 min default
            if md.asset_class == "CRYPTO": limit = 10000 # 10s for crypto
            
            if md.data_age_ms > limit:
                md.status = "DELAYED"
            
            return md.to_dict()
        return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        provider = self._get_provider(symbol)
        if not provider:
            return pd.DataFrame()
        return await provider.fetch_ohlcv(symbol, timeframe, limit)

    async def get_market_overview(self) -> Dict[str, List[Dict[str, Any]]]:
        symbols = self.catalog.get_all_symbols()
        tasks = [self.fetch_ticker(s) for s in symbols]
        results = await asyncio.gather(*tasks)
        
        overview = {"CRYPTO": [], "FOREX": [], "COMMODITY": [], "INDEX": []}
        for res in results:
            if res:
                overview[res["asset_class"]].append(res)
        return overview

    async def fetch_crypto_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        # Legacy compatibility for api/index.py
        return await self.fetch_ohlcv(symbol, timeframe, limit)

    async def fetch_crypto_price(self, symbol: str) -> Dict[str, Any]:
        # Legacy compatibility
        ticker = await self.fetch_ticker(symbol)
        return ticker or {"error": "unavailable", "symbol": symbol}
