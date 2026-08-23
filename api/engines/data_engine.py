"""Market-data orchestrator and source reliability cascade."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List

from .data_health import DataHealthMonitor
from .data_layer import DataLayer
from .data_providers.binance_provider import BinanceProvider
from .data_providers.bybit_provider import BybitProvider
from .data_providers.coinbase_provider import CoinbaseProvider
from .data_providers.finnhub_provider import FinnhubProvider
from .data_providers.gate_provider import GateProvider
from .data_providers.kraken_provider import KrakenProvider
from .data_providers.okx_provider import OKXProvider
from .data_providers.twelvedata_provider import TwelveDataProvider
from .data_providers.yahoo_provider import YahooProvider
from .market_universe import MarketUniverse


class DataEngine:
    """Manage normalized market data without weakening freshness safeguards."""

    REALTIME_PROVIDERS = (
        "binance", "bybit", "okx", "kraken", "coinbase", "gate",
        "twelvedata", "finnhub",
    )
    CRYPTO_PROVIDERS = ("binance", "bybit", "okx", "kraken", "coinbase", "gate")

    def __init__(self):
        self.layer = DataLayer()
        self.universe = MarketUniverse()

        # Public no-key crypto feeds. Registration order does not determine
        # routing; provider_priority.py owns the exact cascade.
        self.crypto_binance = BinanceProvider()
        self.crypto_bybit = BybitProvider()
        self.crypto_okx = OKXProvider()
        self.crypto_kraken = KrakenProvider()
        self.crypto_coinbase = CoinbaseProvider()
        self.crypto_gate = GateProvider()
        # Backward-compatible attributes used by existing integrations.
        self.crypto_primary = self.crypto_gate
        self.crypto_backup = self.crypto_bybit
        self.crypto_tertiary = self.crypto_binance

        for provider_id, provider in (
            ("binance", self.crypto_binance), ("bybit", self.crypto_bybit),
            ("okx", self.crypto_okx), ("kraken", self.crypto_kraken),
            ("coinbase", self.crypto_coinbase), ("gate", self.crypto_gate),
        ):
            self.layer.register_provider(provider_id, provider)

        self.yahoo_providers = {
            "yahoo_forex": YahooProvider("FOREX"),
            "yahoo_indices": YahooProvider("INDICES"),
            "yahoo_commodities": YahooProvider("COMMODITIES"),
            "yahoo_stocks": YahooProvider("STOCKS"),
            "yahoo_futures": YahooProvider("FUTURES"),
            "yahoo_bonds": YahooProvider("BONDS"),
            "yahoo_etfs": YahooProvider("ETFS"),
        }
        for provider_id, provider in self.yahoo_providers.items():
            self.layer.register_provider(provider_id, provider)

        # Preserve the old public provider attributes.
        self.forex_provider = self.yahoo_providers["yahoo_forex"]
        self.index_provider = self.yahoo_providers["yahoo_indices"]
        self.commodity_provider = self.yahoo_providers["yahoo_commodities"]
        self.stock_provider = self.yahoo_providers["yahoo_stocks"]
        self.futures_provider = self.yahoo_providers["yahoo_futures"]
        self.bonds_provider = self.yahoo_providers["yahoo_bonds"]
        self.etfs_provider = self.yahoo_providers["yahoo_etfs"]

        self.twelvedata_provider = None
        self.finnhub_provider = None
        twelve_key = os.getenv("TWELVEDATA_API_KEY", "").strip()
        finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
        if twelve_key:
            self.twelvedata_provider = TwelveDataProvider(twelve_key)
            self.layer.register_provider("twelvedata", self.twelvedata_provider)
        if finnhub_key:
            self.finnhub_provider = FinnhubProvider(finnhub_key)
            self.layer.register_provider("finnhub", self.finnhub_provider)

        self._init_symbol_map()
        self.health_monitor = DataHealthMonitor(self.layer.providers)

    def set_ws_manager(self, manager: Any) -> None:
        self.layer.subscribers.append(manager)

    def _init_symbol_map(self) -> None:
        """Ensure six-way crypto redundancy and optional tradfi mappings."""
        for market_id in self.universe.get_all_ids():
            info = self.universe.get_info(market_id) or {}
            providers = info.setdefault("providers", {})
            if info.get("asset_class") == "CRYPTO":
                primary = next(iter(providers.values()), None)
                if primary:
                    # A provider may reject an uncommon asset; DataLayer then
                    # falls through to the next feed with symbol cooldown.
                    for provider_id in self.CRYPTO_PROVIDERS:
                        providers.setdefault(provider_id, primary)
            else:
                yahoo_symbol = next(
                    (symbol for pid, symbol in providers.items() if pid.startswith("yahoo_")),
                    None,
                )
                if yahoo_symbol and self.twelvedata_provider:
                    providers.setdefault("twelvedata", self._twelvedata_symbol(yahoo_symbol))
                if yahoo_symbol and self.finnhub_provider:
                    providers.setdefault("finnhub", self._finnhub_symbol(yahoo_symbol, info))

            for provider_id in providers:
                if provider_id in self.layer.providers:
                    self.layer.symbol_map[market_id] = provider_id
                    break

    @staticmethod
    def _twelvedata_symbol(symbol: str) -> str:
        if symbol.endswith("=X") and len(symbol) >= 8:
            raw = symbol[:-2]
            return f"{raw[:3]}/{raw[3:6]}"
        return symbol

    @staticmethod
    def _finnhub_symbol(symbol: str, info: Dict[str, Any]) -> str:
        if info.get("asset_class") == "FOREX" and symbol.endswith("=X"):
            raw = symbol[:-2]
            return f"OANDA:{raw[:3]}_{raw[3:6]}"
        return symbol

    async def prepare_scan_cycle(self, market_ids: List[str]) -> None:
        """Prime each Yahoo asset class once before its scan phase."""
        grouped: Dict[str, List[str]] = {}
        for market_id in market_ids:
            info = self.universe.get_info(market_id) or {}
            for provider_id, symbol in info.get("providers", {}).items():
                if provider_id.startswith("yahoo_") and provider_id in self.yahoo_providers:
                    grouped.setdefault(provider_id, []).append(symbol)
                    break
        await asyncio.gather(*(
            asyncio.wait_for(
                self.yahoo_providers[provider_id].prepare_cycle(symbols),
                timeout=self.layer.provider_timeout_s * 2,
            )
            for provider_id, symbols in grouped.items()
        ), return_exceptions=True)

    def is_realtime_capable(self, market_id: str) -> bool:
        info = self.universe.get_info(market_id)
        if not info:
            return False
        return any(provider_id in self.REALTIME_PROVIDERS
                   and provider_id in self.layer.providers
                   for provider_id in info.get("providers", {}))

    def is_quote_realtime(self, market_id: str, ticker: Dict[str, Any]) -> bool:
        if not ticker or str(ticker.get("status", "")).upper() != "LIVE":
            return False
        state = getattr(self.layer, "market_source_state", {}).get(market_id) or {}
        provider_id = state.get("provider_id")
        if provider_id:
            return provider_id in self.REALTIME_PROVIDERS
        source = str(ticker.get("source") or "").lower()
        return "yahoo" not in source and "delayed" not in source

    def check_scalping_allowed(self, market_id: str,
                               allow_delayed: bool = False) -> Dict[str, Any]:
        source_state = getattr(self.layer, "market_source_state", {}).get(market_id) or {}
        active_provider = source_state.get("provider_id")
        # When a quote has already been fetched, guard the source actually in
        # use rather than a merely configured optional provider. This prevents
        # a Yahoo fallback from becoming tradable just because a keyed provider
        # is present but unavailable.
        realtime = (active_provider in self.REALTIME_PROVIDERS
                    if active_provider else self.is_realtime_capable(market_id))
        if realtime:
            return {"allowed": True, "reason": None, "realtime": True}
        if allow_delayed:
            return {"allowed": True, "reason": "Delayed data source explicitly allowed",
                    "realtime": False}
        return {"allowed": False, "realtime": False,
                "reason": "NON_REALTIME_SOURCE — scalping blocked on delayed data (Yahoo)"}

    async def broadcast_market_update(self, market_id: str) -> None:
        info = self.universe.get_info(market_id)
        if not info:
            return
        ticker = await self.fetch_ticker(market_id)
        if ticker:
            now_ms = int(datetime.now().timestamp() * 1000)
            timestamp = ticker.get("timestamp") or now_ms
            await self.layer.broadcast_update({
                "type": "MARKET_UPDATE", "market_id": market_id,
                "display_symbol": info["display_symbol"], "price": ticker["last"],
                "status": ticker["status"], "timestamp": ticker["timestamp"],
                "data_age_ms": max(0, now_ms - int(timestamp)),
                "change_24h": ticker.get("change_24h"), "volume": ticker.get("volume"),
                "realtime_source": self.is_quote_realtime(market_id, ticker),
            })

    async def get_market_overview(self) -> Dict[str, List[Dict[str, Any]]]:
        ids = self.universe.get_all_ids()
        quotes = await self.layer.get_all_quotes(ids, self.universe)
        overview = {category: [] for category in self.universe.ASSET_CLASSES}
        now_ms = int(datetime.now().timestamp() * 1000)
        for quote in quotes:
            market_id = "unknown"
            for candidate in ids:
                info = self.universe.get_info(candidate) or {}
                if quote.symbol in info.get("providers", {}).values():
                    market_id = candidate
                    break
            info = self.universe.get_info(market_id) or {}
            asset_class = info.get("asset_class")
            if asset_class not in overview:
                continue
            payload = quote.model_dump()
            payload.update({
                "market_id": market_id,
                "display_symbol": info.get("display_symbol"),
                "underlying": info.get("underlying", market_id),
                "name": info.get("name", quote.symbol),
                "tick_size": info.get("tick_size"),
                "leverage_max": info.get("leverage_max"),
                "market_status": self.universe.get_market_status(market_id),
                "data_age_ms": max(0, now_ms - int(quote.timestamp)),
                "realtime_source": self.is_quote_realtime(market_id, payload),
                "active_source": payload.get("source"),
                "available_sources": self.available_source_count(market_id),
            })
            overview[asset_class].append(payload)
        return overview

    def available_source_count(self, market_id: str) -> int:
        info = self.universe.get_info(market_id) or {}
        registered = getattr(self.layer, "providers", {})
        # Lightweight test/integration layers may not expose a registry; in
        # that case the catalog still describes the available mappings.
        if not registered:
            return len(info.get("providers", {}))
        return sum(1 for provider_id in info.get("providers", {})
                   if provider_id in registered)

    def get_market_source_health(self) -> List[Dict[str, Any]]:
        now_ms = int(datetime.now().timestamp() * 1000)
        report = []
        for market_id in self.universe.get_all_ids():
            info = self.universe.get_info(market_id) or {}
            state = getattr(self.layer, "market_source_state", {}).get(market_id) or {}
            timestamp = state.get("timestamp")
            report.append({
                "market_id": market_id,
                "display_symbol": info.get("display_symbol"),
                "underlying": info.get("underlying", market_id),
                "active_source": state.get("source"),
                "active_provider": state.get("provider_id"),
                "age_ms": max(0, now_ms - int(timestamp)) if timestamp else None,
                "available_sources": self.available_source_count(market_id),
            })
        return report

    async def fetch_ohlcv(self, market_id: str, timeframe: str = "1m", limit: int = 100):
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
        if not ticker or "timestamp" not in ticker:
            return False
        age_ms = int(datetime.now().timestamp() * 1000) - ticker["timestamp"]
        return age_ms < (5000 if asset_class == "CRYPTO" else 60000)

    async def shutdown(self) -> None:
        providers = [self.layer.providers[provider_id] for provider_id in self.CRYPTO_PROVIDERS]
        await asyncio.gather(*(provider.close() for provider in providers), return_exceptions=True)
