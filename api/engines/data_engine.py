from .data_layer import DataLayer
from .market_universe import MarketUniverse
from .data_providers.bybit_provider import BybitProvider
from .data_providers.gate_provider import GateProvider
from .data_providers.yahoo_provider import YahooProvider
from .data_health import DataHealthMonitor
from typing import Dict, Any, List, Optional
import asyncio
import pandas as pd
import json
from datetime import datetime

class DataEngine:
    """
    Market Data Orchestrator (Rule 4, 15, 17, 18).
    Manages multiple providers via DataLayer and handles normalization.
    """
    def __init__(self):
        self.layer = DataLayer()
        self.universe = MarketUniverse()
        
        # Initialize Providers (Rule 10)
        self.crypto_primary = GateProvider() 
        self.crypto_backup = BybitProvider()
        self.forex_provider = YahooProvider("FOREX")
        self.index_provider = YahooProvider("INDICES")
        self.commodity_provider = YahooProvider("COMMODITIES")
        
        # Register in Layer
        self.layer.register_provider("gate", self.crypto_primary)
        self.layer.register_provider("bybit", self.crypto_backup)
        self.layer.register_provider("yahoo_forex", self.forex_provider)
        self.layer.register_provider("yahoo_indices", self.index_provider)
        self.layer.register_provider("yahoo_commodities", self.commodity_provider)
        
        # Initialize Health Monitor (Rule 39)
        self.health_monitor = DataHealthMonitor(self.layer.providers)
        
        # Initialize Symbol Mapping (Rule 14)
        self._init_symbol_map()

    def set_ws_manager(self, manager: Any):
        """Connect DataLayer to WebSocket for real-time broadcast."""
        self.layer.subscribers.append(manager)

    async def broadcast_market_update(self, market_id: str):
        """Rule 20, 22: Broadcast a specific market update to the bus."""
        info = self.universe.get_info(market_id)
        if not info: return
        
        # Simple polling-to-broadcast for demonstration (WS native Lot 12)
        ticker = await self.fetch_ticker(market_id)
        if ticker:
            update = {
                "type": "MARKET_UPDATE",
                "market_id": market_id,
                "display_symbol": info["display_symbol"],
                "price": ticker["last"],
                "status": ticker["status"],
                "timestamp": ticker["timestamp"]
            }
            await self.layer.broadcast_update(update)

    def _init_symbol_map(self):
        for market_id in self.universe.get_all_ids():
            info = self.universe.get_info(market_id)
            # Register first available provider for each market in DataLayer mapping
            for pid in info.get("providers", {}).keys():
                if pid in self.layer.providers:
                    self.layer.symbol_map[market_id] = pid
                    break

    async def get_market_overview(self) -> Dict[str, List[Dict[str, Any]]]:
        """Unified market overview (Rule 12, 13)."""
        ids = self.universe.get_all_ids()
        # Batch fetching quotes via DataLayer
        quotes = await self.layer.get_all_quotes(ids, self.universe)
        
        overview = {cat: [] for cat in self.universe.ASSET_CLASSES}
        for q in quotes:
            # Reverse mapping to find market_id
            market_id = "unknown"
            for mid in ids:
                info = self.universe.get_info(mid)
                if q.symbol in info.get("providers", {}).values():
                    market_id = mid
                    break

            info = self.universe.get_info(market_id)
            asset_class = info.get("asset_class")
            if asset_class in overview:
                q_dict = q.dict()
                # Operational status (Rule 11)
                q_dict.update({
                    "market_id": market_id,
                    "display_symbol": info.get("display_symbol"),
                    "name": info.get("name", q.symbol),
                    "tick_size": info.get("tick_size"),
                    "leverage_max": info.get("leverage_max"),
                    "market_status": self.universe.get_market_status(market_id)
                })
                overview[asset_class].append(q_dict)
        return overview

    async def fetch_ohlcv(self, market_id: str, timeframe: str = '1m', limit: int = 100):
        # Rule 25: Map market_id to its providers and fetch with fallback
        return await self.layer.get_ohlcv(market_id, timeframe, limit, self.universe)

    async def fetch_ticker(self, market_id: str):
        quotes = await self.layer.get_all_quotes([market_id], self.universe)
        return quotes[0].dict() if quotes else None

    # Legacy method compatibility
    async def fetch_crypto_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100):
        return await self.fetch_ohlcv(symbol, timeframe, limit)

    async def fetch_crypto_price(self, symbol: str):
        return await self.fetch_ticker(symbol)
        
    async def shutdown(self):
        await self.crypto_primary.close()
        await self.crypto_backup.close()
