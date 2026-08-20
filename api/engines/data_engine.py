from .data_layer import DataLayer
from .market_catalog import MarketCatalog
from .data_providers.binance_provider import BinanceProvider
from .data_providers.gate_provider import GateProvider
from .data_providers.yahoo_provider import YahooProvider
from typing import Dict, Any, List, Optional
import asyncio
from datetime import datetime

class DataEngine:
    """
    Market Data Orchestrator.
    Handles discovery, normalization and freshness validation (Rule 4, 15, 17, 18).
    """
    def __init__(self):
        self.layer = DataLayer()
        self.catalog = MarketCatalog()
        
        # Initialize Providers
        self.crypto_provider = GateProvider() # Gate.io as primary crypto
        self.forex_provider = YahooProvider("FOREX")
        self.index_provider = YahooProvider("INDICES")
        self.commodity_provider = YahooProvider("COMMODITIES")
        
        # Register in Layer
        self.layer.register_provider("gate", self.crypto_provider)
        self.layer.register_provider("yahoo_forex", self.forex_provider)
        self.layer.register_provider("yahoo_indices", self.index_provider)
        self.layer.register_provider("yahoo_commodities", self.commodity_provider)
        
        # Initialize Symbol Mapping
        self._init_symbol_map()

    def _init_symbol_map(self):
        for symbol in self.catalog.get_all_ids():
            info = self.catalog.get_info(symbol)
            asset_class = info.get("asset_class")
            
            if asset_class == "CRYPTO":
                self.layer.symbol_map[symbol] = "gate"
            elif asset_class == "FOREX":
                self.layer.symbol_map[symbol] = "yahoo_forex"
            elif asset_class == "INDICES":
                self.layer.symbol_map[symbol] = "yahoo_indices"
            elif asset_class == "COMMODITIES":
                self.layer.symbol_map[symbol] = "yahoo_commodities"

    async def get_market_overview(self) -> Dict[str, List[Dict[str, Any]]]:
        """Unified market overview (Rule 12, 13)."""
        ids = self.catalog.get_all_ids()
        # Batch fetching to respect rate limits (Rule 43)
        batch_size = 5
        quotes = []
        for i in range(0, len(ids), batch_size):
            batch = ids[i:i+batch_size]
            batch_quotes = await self.layer.get_all_quotes(batch, self.catalog)
            quotes.extend(batch_quotes)
            await asyncio.sleep(0.1) 
        
        overview = {cat: [] for cat in self.catalog.get_categories()}
        for q in quotes:
            # We need to find the market_id back from the provider symbol
            # Simplification: we'll attach market_id to q in DataLayer soon.
            # For now, let's search in catalog.
            market_id = "unknown"
            for mid in ids:
                info = self.catalog.get_info(mid)
                if q.symbol in info.get("providers", {}).values():
                    market_id = mid
                    break

            info = self.catalog.get_info(market_id)
            asset_class = info.get("asset_class")
            if asset_class in overview:
                q_dict = q.dict()
                q_dict.update({
                    "market_id": market_id,
                    "display_symbol": info.get("display_symbol"),
                    "name": info.get("name", q.symbol),
                    "tick_size": info.get("tick_size"),
                    "leverage": info.get("leverage")
                })
                overview[asset_class].append(q_dict)
        return overview

    async def fetch_ohlcv(self, market_id: str, timeframe: str = '1m', limit: int = 100):
        # Map market_id to first available provider symbol
        info = self.catalog.get_info(market_id)
        if not info: return pd.DataFrame()
        
        for pid, psymbol in info.get("providers", {}).items():
            if pid in self.layer.providers:
                return await self.layer.providers[pid].get_ohlcv(psymbol, timeframe, limit)
        return pd.DataFrame()

    async def fetch_ticker(self, market_id: str):
        quotes = await self.layer.get_all_quotes([market_id], self.catalog)
        return quotes[0].dict() if quotes else None

    # Legacy method compatibility
    async def fetch_crypto_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100):
        return await self.fetch_ohlcv(symbol, timeframe, limit)

    async def fetch_crypto_price(self, symbol: str):
        return await self.fetch_ticker(symbol)
        
    async def shutdown(self):
        await self.crypto_provider.close()
