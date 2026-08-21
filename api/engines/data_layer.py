from typing import Dict, List, Optional, Any
from .data_providers.base_provider import MarketDataProvider, TickerModel
import pandas as pd
import asyncio
import json
import time

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
        self.failure_cache: Dict[str, float] = {} # {psymbol: timestamp}
        self.failure_cooldown = 300 # 5 minutes cooldown for failed symbols

    def register_provider(self, provider_id: str, provider: MarketDataProvider):
        self.providers[provider_id] = provider

    async def broadcast_update(self, update_data: Dict[str, Any]):
        """Rule 20: MarketDataBus implementation."""
        for sub in self.subscribers:
            if hasattr(sub, "broadcast"):
                try:
                    await sub.broadcast(json.dumps(update_data))
                except Exception:
                    pass

    async def get_all_quotes(self, market_ids: List[str], catalog: Any) -> List[TickerModel]:
        """
        Fetches quotes with automatic fallback logic (Lot 2 Redundancy).
        """
        results = []
        now = time.time()
        for mid in market_ids:
            info = catalog.get_info(mid)
            if not info: continue
            
            # Sorted providers list: we try them in order
            provider_list = list(info.get("providers", {}).items())
            
            quote = None
            for pid, psymbol in provider_list:
                # Failure cache check (Rule 3.5 - Throttling)
                cache_key = f"{pid}:{psymbol}"
                if cache_key in self.failure_cache:
                    if now - self.failure_cache[cache_key] < self.failure_cooldown:
                        continue # Skip this provider for this symbol
                    else:
                        del self.failure_cache[cache_key]

                if pid in self.providers:
                    try:
                        quote = await self.providers[pid].get_quote(psymbol)
                        if quote:
                            break
                        else:
                            # Silently fail if fallback exists, only log if critical (Rule 3.4)
                            self.failure_cache[cache_key] = now
                    except Exception as e:
                        # Log as warning/debug if it's a known delisted noise (Rule 3.4)
                        if "possibly delisted" in str(e) or "No data found" in str(e):
                            # Silence yfinance noise
                            pass
                        else:
                            # Generic failure log reduced to warning
                            # print(f"Provider {pid} failed for {psymbol}: {e}")
                            pass
                        self.failure_cache[cache_key] = now
            
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

        now = time.time()
        for pid, psymbol in info.get("providers", {}).items():
            cache_key = f"ohlcv:{pid}:{psymbol}"
            if cache_key in self.failure_cache:
                if now - self.failure_cache[cache_key] < self.failure_cooldown:
                    continue
                else:
                    del self.failure_cache[cache_key]

            if pid in self.providers:
                try:
                    df = await self.providers[pid].get_ohlcv(psymbol, timeframe, limit)
                    if not df.empty:
                        return df
                    else:
                        self.failure_cache[cache_key] = now
                except Exception:
                    self.failure_cache[cache_key] = now
        
        return pd.DataFrame()

    async def get_order_book(self, market_id: str, catalog: Any) -> Optional[Dict[str, Any]]:
        info = catalog.get_info(market_id)
        if not info: return None
        for pid, psymbol in info.get("providers", {}).items():
            if pid in self.providers:
                try:
                    ob = await self.providers[pid].get_order_book(psymbol)
                    if ob: return ob
                except: continue
        return None

    async def get_trades(self, market_id: str, catalog: Any) -> Optional[List[Dict[str, Any]]]:
        info = catalog.get_info(market_id)
        if not info: return None
        for pid, psymbol in info.get("providers", {}).items():
            if pid in self.providers:
                try:
                    t = await self.providers[pid].get_recent_trades(psymbol)
                    if t: return t
                except: continue
        return None

    async def get_cross_quotes(self, market_id: str, catalog: Any) -> List[Dict[str, Any]]:
        """Fetch quotes from all available providers for a single market (Lot 5)."""
        info = catalog.get_info(market_id)
        if not info: return []
        
        provider_list = list(info.get("providers", {}).items())
        tasks = []
        for pid, psymbol in provider_list:
            if pid in self.providers:
                tasks.append(self.providers[pid].get_quote(psymbol))
        
        quotes = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for i, q in enumerate(quotes):
            if isinstance(q, TickerModel):
                d = q.model_dump()
                d["provider"] = provider_list[i][0]
                results.append(d)
        return results

    async def get_health(self) -> List[Dict[str, Any]]:
        tasks = [p.health_check() for p in self.providers.values()]
        return await asyncio.gather(*tasks)
