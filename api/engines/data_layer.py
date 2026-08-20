from typing import Dict, List, Optional, Any
from .data_providers.base_provider import MarketDataProvider, TickerModel
import pandas as pd
import asyncio

class DataIntegrityError(Exception):
    pass

class DataLayer:
    """
    Market Data Layer (Rule 2).
    Independent architecture managing multiple data providers.
    """
    def __init__(self):
        self.providers: Dict[str, MarketDataProvider] = {}
        self.symbol_map: Dict[str, str] = {} # market_id -> provider_id

    def register_provider(self, provider_id: str, provider: MarketDataProvider):
        self.providers[provider_id] = provider

    async def get_all_quotes(self, market_ids: List[str], catalog: Any) -> List[TickerModel]:
        tasks = []
        for mid in market_ids:
            # Rule 14: Try to find which provider has this symbol
            info = catalog.get_info(mid)
            if not info: continue
            
            for pid, psymbol in info.get("providers", {}).items():
                if pid in self.providers:
                    # We create a task for the first available provider found
                    tasks.append(self.providers[pid].get_quote(psymbol))
                    break
        
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def get_ohlcv(self, symbol_id: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        provider_id = self.symbol_map.get(symbol_id)
        if provider_id and provider_id in self.providers:
            return await self.providers[provider_id].get_ohlcv(symbol_id, timeframe, limit)
        return pd.DataFrame()

    async def get_health(self) -> List[Dict[str, Any]]:
        tasks = [p.health_check() for p in self.providers.values()]
        return await asyncio.gather(*tasks)
