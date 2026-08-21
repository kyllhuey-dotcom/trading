from typing import Dict, List, Optional, Any
from .data_providers.base_provider import MarketDataProvider, TickerModel
import pandas as pd
import asyncio
import json

class DataIntegrityError(Exception):
    pass

class DataLayer:
    """
    Market Data Layer (Rule 2).
    Independent architecture managing multiple data providers.
    """
    def __init__(self):
        self.providers: Dict[str, MarketDataProvider] = {}
        self.symbol_map: Dict[str, str] = {} 
        self.subscribers: List[Any] = [] # WebSocket managers or other engines

    def register_provider(self, provider_id: str, provider: MarketDataProvider):
        self.providers[provider_id] = provider

    async def broadcast_update(self, update_data: Dict[str, Any]):
        """Rule 20: MarketDataBus implementation."""
        for sub in self.subscribers:
            if hasattr(sub, "broadcast"):
                await sub.broadcast(json.dumps(update_data))

    async def get_all_quotes(self, market_ids: List[str], catalog: Any) -> List[TickerModel]:
        """
        Fetches quotes with automatic fallback logic (Lot 2 Redundancy).
        """
        results = []
        for mid in market_ids:
            info = catalog.get_info(mid)
            if not info: continue
            
            # Sorted providers list: we try them in order
            provider_list = list(info.get("providers", {}).items())
            
            quote = None
            for pid, psymbol in provider_list:
                if pid in self.providers:
                    try:
                        quote = await self.providers[pid].get_quote(psymbol)
                        if quote:
                            # Successfully fetched from this provider
                            break
                        else:
                            print(f"Fallback: Provider {pid} returned no data for {psymbol}. Trying next...")
                    except Exception as e:
                        print(f"Fallback: Provider {pid} failed for {psymbol}: {e}. Trying next...")
            
            if quote:
                results.append(quote)
        
        return results

    async def get_ohlcv(self, symbol_id: str, timeframe: str, limit: int = 100, catalog: Any = None) -> pd.DataFrame:
        """
        Fetches OHLCV with fallback logic.
        """
        if not catalog: return pd.DataFrame()
        
        info = catalog.get_info(symbol_id)
        if not info: return pd.DataFrame()

        for pid, psymbol in info.get("providers", {}).items():
            if pid in self.providers:
                try:
                    df = await self.providers[pid].get_ohlcv(psymbol, timeframe, limit)
                    if not df.empty:
                        return df
                except Exception as e:
                    print(f"OHLCV Fallback: {pid} failed: {e}")
        
        return pd.DataFrame()

    async def get_health(self) -> List[Dict[str, Any]]:
        tasks = [p.health_check() for p in self.providers.values()]
        return await asyncio.gather(*tasks)
