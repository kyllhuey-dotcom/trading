from .data_layer import DataLayer
from .market_universe import MarketUniverse
from .data_providers.bybit_provider import BybitProvider
from .data_providers.gate_provider import GateProvider
from .data_providers.binance_provider import BinanceProvider
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
        
        # Initialize Providers (Rule 10) — crypto gets 3-way redundancy
        self.crypto_primary = GateProvider() 
        self.crypto_backup = BybitProvider()
        self.crypto_tertiary = BinanceProvider()
        self.forex_provider = YahooProvider("FOREX")
        self.index_provider = YahooProvider("INDICES")
        self.commodity_provider = YahooProvider("COMMODITIES")
        self.stock_provider = YahooProvider("STOCKS")
        self.futures_provider = YahooProvider("FUTURES")
        self.bonds_provider = YahooProvider("BONDS")
        self.etfs_provider = YahooProvider("ETFS")
        
        # Register in Layer
        self.layer.register_provider("gate", self.crypto_primary)
        self.layer.register_provider("bybit", self.crypto_backup)
        self.layer.register_provider("binance", self.crypto_tertiary)
        self.layer.register_provider("yahoo_forex", self.forex_provider)
        self.layer.register_provider("yahoo_indices", self.index_provider)
        self.layer.register_provider("yahoo_commodities", self.commodity_provider)
        self.layer.register_provider("yahoo_stocks", self.stock_provider)
        self.layer.register_provider("yahoo_futures", self.futures_provider)
        self.layer.register_provider("yahoo_bonds", self.bonds_provider)
        self.layer.register_provider("yahoo_etfs", self.etfs_provider)
        
        # Initialize Health Monitor (Rule 39)
        self.health_monitor = DataHealthMonitor(self.layer.providers)
        
        # Initialize Symbol Mapping (Rule 14)
        self._init_symbol_map()

    def set_ws_manager(self, manager: Any):
        """Connect DataLayer to WebSocket for real-time broadcast."""
        self.layer.subscribers.append(manager)

    # ------------------------------------------------------------------ #
    # LOT F: non-realtime source guard                                   #
    # ------------------------------------------------------------------ #
    REALTIME_PROVIDERS = ("gate", "bybit", "binance")

    def is_realtime_capable(self, market_id: str) -> bool:
        """True when the instrument is fed by a realtime crypto exchange."""
        info = self.universe.get_info(market_id)
        if not info:
            return False
        return any(pid in self.REALTIME_PROVIDERS for pid in info.get("providers", {}))

    def check_scalping_allowed(self, market_id: str,
                               allow_delayed: bool = False) -> Dict[str, Any]:
        """
        Ultra-scalping needs *realtime* data. Yahoo Finance is delayed
        (~15 min) — auto-trading it as if it were live would be dishonest
        and dangerous. Blocked by default; `allow_delayed_data_trading=true`
        in settings is the explicit opt-out (swing/experimental use only).
        """
        if self.is_realtime_capable(market_id):
            return {"allowed": True, "reason": None, "realtime": True}
        if allow_delayed:
            return {"allowed": True, "reason": "Delayed data source explicitly allowed",
                    "realtime": False}
        return {"allowed": False, "realtime": False,
                "reason": "NON_REALTIME_SOURCE — scalping blocked on delayed data (Yahoo)"}

    async def broadcast_market_update(self, market_id: str):
        """Rule 20, 22: Broadcast a specific market update to the bus."""
        info = self.universe.get_info(market_id)
        if not info: return
        
        # Simple polling-to-broadcast for demonstration (WS native Lot 12)
        ticker = await self.fetch_ticker(market_id)
        if ticker:
            now_ms = int(datetime.now().timestamp() * 1000)
            ts = ticker.get("timestamp") or now_ms
            update = {
                "type": "MARKET_UPDATE",
                "market_id": market_id,
                "display_symbol": info["display_symbol"],
                "price": ticker["last"],
                "status": ticker["status"],
                "timestamp": ticker["timestamp"],
                "data_age_ms": max(0, now_ms - int(ts)),
                "change_24h": ticker.get("change_24h"),
                "volume": ticker.get("volume"),
                "realtime_source": self.is_realtime_capable(market_id),
            }
            await self.layer.broadcast_update(update)

    def _init_symbol_map(self):
        for market_id in self.universe.get_all_ids():
            info = self.universe.get_info(market_id)
            # Automatic redundancy: every CRYPTO market can also fall back to Binance
            # (same "XXX/USDT" symbol format as Gate/Bybit).
            if info.get("asset_class") == "CRYPTO" and "binance" not in info.get("providers", {}):
                primary = info.get("providers", {}).get("gate") or info.get("providers", {}).get("bybit")
                if primary:
                    info["providers"]["binance"] = primary
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
                q_dict = q.model_dump()
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
        return quotes[0].model_dump() if quotes else None

    async def fetch_cross_quotes(self, market_id: str):
        return await self.layer.get_cross_quotes(market_id, self.universe)

    async def fetch_order_book(self, market_id: str):
        return await self.layer.get_order_book(market_id, self.universe)

    async def fetch_trades(self, market_id: str):
        return await self.layer.get_trades(market_id, self.universe)

    def is_fresh(self, ticker: Dict[str, Any], asset_class: str) -> bool:
        """Rule: Verify data freshness before any decision (Lot 3)."""
        if not ticker or "timestamp" not in ticker:
            return False
            
        now_ms = int(datetime.now().timestamp() * 1000)
        age_ms = now_ms - ticker["timestamp"]
        
        if asset_class == "CRYPTO":
            return age_ms < 5000 # 5 seconds
        else:
            # Forex/Indices/etc are often delayed by 15min at source, 
            # but we check if our last FETCH was recent.
            return age_ms < 60000 # 60 seconds
            
    async def shutdown(self):
        await self.crypto_primary.close()
        await self.crypto_backup.close()
        await self.crypto_tertiary.close()
