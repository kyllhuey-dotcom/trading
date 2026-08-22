from typing import Dict, List, Optional, Any
from .data_providers.base_provider import MarketDataProvider, TickerModel
from .provider_priority import prioritize_providers
import pandas as pd
import asyncio
import json
import time
import logging

logger = logging.getLogger("DataLayer")

class DataIntegrityError(Exception):
    pass

class DataLayer:
    """
    Market Data Layer (Rule 2).
    Independent architecture managing multiple data providers with fallback.

    LOT F hardening:
    - per-provider strict timeouts on every fetch path (a hung provider can
      no longer stall the trading loops);
    - failure cooldown with exponential escalation: each consecutive failure
      doubles the cooldown (5 min base, 60 min cap) and success resets it.
    """
    def __init__(self):
        self.providers: Dict[str, MarketDataProvider] = {}
        self.symbol_map: Dict[str, str] = {} 
        self.subscribers: List[Any] = [] # WebSocket managers or other engines
        self.failure_cache: Dict[str, float] = {} # {cache_key: timestamp}
        self.failure_counts: Dict[str, int] = {} # {cache_key: consecutive failures}
        self.failure_cooldown = 300 # 5 minutes base cooldown for failed symbols
        self.max_failure_cooldown = 3600 # escalation cap: 60 minutes
        # LOT B/LOT F: strict per-provider timeout (quotes + orderbook + trades).
        self.provider_timeout_s = 5.0
        self._quote_cache: Dict[str, tuple] = {}
        self._quote_cache_ttl = 0.25

    # ------------------------------------------------------------------ #
    # Failure tracking (escalating cooldown)                             #
    # ------------------------------------------------------------------ #
    def _record_failure(self, key: str) -> None:
        self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
        self.failure_cache[key] = time.time()

    def _record_success(self, key: str) -> None:
        self.failure_counts.pop(key, None)
        self.failure_cache.pop(key, None)

    def _cooldown_for(self, key: str) -> float:
        """Base cooldown × 2^(consecutive failures - 1), capped."""
        n = max(0, self.failure_counts.get(key, 0))
        return min(self.max_failure_cooldown, self.failure_cooldown * (2 ** max(0, n - 1)))

    def _in_cooldown(self, key: str) -> bool:
        ts = self.failure_cache.get(key)
        if ts is None:
            return False
        if time.time() - ts < self._cooldown_for(key):
            return True
        del self.failure_cache[key]
        self.failure_counts.pop(key, None)
        return False

    def _prune_failure_cache(self) -> None:
        """Drop expired entries so the cache cannot grow unbounded."""
        if len(self.failure_cache) > 2000:
            now = time.time()
            self.failure_cache = {k: v for k, v in self.failure_cache.items()
                                  if now - v < self.max_failure_cooldown}
            self.failure_counts = {k: v for k, v in self.failure_counts.items()
                                   if k in self.failure_cache}

    def register_provider(self, provider_id: str, provider: MarketDataProvider):
        self.providers[provider_id] = provider

    async def broadcast_update(self, update_data: Dict[str, Any]):
        """Rule 20: MarketDataBus implementation."""
        for sub in self.subscribers:
            if hasattr(sub, "broadcast"):
                try:
                    await sub.broadcast(json.dumps(update_data))
                except Exception as exc:
                    logger.debug("Market-data subscriber broadcast failed: %s", exc)

    async def _fetch_quote_with_timeout(self, provider: MarketDataProvider,
                                        psymbol: str) -> Optional[TickerModel]:
        return await asyncio.wait_for(provider.get_quote(psymbol),
                                      timeout=self.provider_timeout_s)

    async def get_all_quotes(self, market_ids: List[str], catalog: Any) -> List[TickerModel]:
        """
        Fetches quotes with automatic fallback logic (Lot 2 Redundancy).
        """
        now = time.time()
        self._prune_failure_cache()
        # Drop expired quote cache.  An overview can contain more than one
        # hundred markets: fetching them serially made the endpoint take up to
        # 5 seconds *per market* whenever a provider was unavailable.
        self._quote_cache = {k: v for k, v in self._quote_cache.items()
                             if now - v[0] < self._quote_cache_ttl}

        async def fetch_one(mid: str) -> Optional[TickerModel]:
            info = catalog.get_info(mid)
            if not info:
                return None
            cached = self._quote_cache.get(mid)
            if cached and now - cached[0] < self._quote_cache_ttl:
                return cached[1]

            for pid, psymbol in prioritize_providers(info.get("providers", {}).items()):
                # Failure cache check (Rule 3.5 - Throttling, LOT F escalation)
                cache_key = f"{pid}:{psymbol}"
                if self._in_cooldown(cache_key) or pid not in self.providers:
                    continue
                try:
                    quote = await self._fetch_quote_with_timeout(self.providers[pid], psymbol)
                    if quote:
                        self._record_success(cache_key)
                        self._quote_cache[mid] = (now, quote)
                        return quote
                    self._record_failure(cache_key)
                except Exception as e:
                    # Known delisted-noise from yfinance stays silent, the rest is logged.
                    if "possibly delisted" not in str(e) and "No data found" not in str(e):
                        logger.debug(f"Provider {pid} failed for {psymbol}: {e}")
                    self._record_failure(cache_key)
            return None

        # Keep the input order (useful to callers and tests) while allowing
        # independent provider requests to share one bounded timeout window.
        quotes = await asyncio.gather(*(fetch_one(mid) for mid in market_ids))
        return [quote for quote in quotes if quote is not None]

    async def get_ohlcv(self, symbol_id: str, timeframe: str, limit: int = 100, catalog: Any = None) -> pd.DataFrame:
        """
        Fetches OHLCV with fallback logic.
        """
        if not catalog:
            return pd.DataFrame()
        
        info = catalog.get_info(symbol_id)
        if not info:
            return pd.DataFrame()

        self._prune_failure_cache()
        for pid, psymbol in prioritize_providers(info.get("providers", {}).items()):
            cache_key = f"ohlcv:{pid}:{psymbol}"
            if self._in_cooldown(cache_key):
                continue

            if pid in self.providers:
                try:
                    df = await asyncio.wait_for(
                        self.providers[pid].get_ohlcv(psymbol, timeframe, limit),
                        timeout=self.provider_timeout_s * 2)
                    if not df.empty:
                        self._record_success(cache_key)
                        return df
                    else:
                        self._record_failure(cache_key)
                except Exception:
                    self._record_failure(cache_key)
        
        return pd.DataFrame()

    async def get_order_book(self, market_id: str, catalog: Any) -> Optional[Dict[str, Any]]:
        info = catalog.get_info(market_id)
        if not info:
            return None
        for pid, psymbol in prioritize_providers(info.get("providers", {}).items()):
            cache_key = f"ob:{pid}:{psymbol}"
            if self._in_cooldown(cache_key):
                continue
            if pid in self.providers:
                try:
                    ob = await asyncio.wait_for(
                        self.providers[pid].get_order_book(psymbol),
                        timeout=self.provider_timeout_s)
                    if ob:
                        self._record_success(cache_key)
                        return ob
                    self._record_failure(cache_key)
                except Exception as exc:
                    logger.debug("Order book failed (%s:%s): %s", pid, psymbol, exc)
                    self._record_failure(cache_key)
                    continue
        return None

    async def get_trades(self, market_id: str, catalog: Any) -> Optional[List[Dict[str, Any]]]:
        info = catalog.get_info(market_id)
        if not info:
            return None
        for pid, psymbol in prioritize_providers(info.get("providers", {}).items()):
            cache_key = f"tr:{pid}:{psymbol}"
            if self._in_cooldown(cache_key):
                continue
            if pid in self.providers:
                try:
                    t = await asyncio.wait_for(
                        self.providers[pid].get_recent_trades(psymbol),
                        timeout=self.provider_timeout_s)
                    if t:
                        self._record_success(cache_key)
                        return t
                    self._record_failure(cache_key)
                except Exception as exc:
                    logger.debug("Recent trades failed (%s:%s): %s", pid, psymbol, exc)
                    self._record_failure(cache_key)
                    continue
        return None

    async def get_cross_quotes(self, market_id: str, catalog: Any,
                               timeout_s: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Fetch quotes from all available providers for a single market (Lot 5).

        LOT B hardening:
        - strict per-provider timeout (a slow provider can no longer stall the batch);
        - each quote carries `latency_ms` (fetch duration), `received_at` (epoch ms)
          and `age_ms` (data age vs provider timestamp) so the arbitrage strategy
          can score confidence and drop unsynchronized quotes;
        - failed providers go into the failure cache (cooldown, like other paths).
        """
        info = catalog.get_info(market_id)
        if not info:
            return []

        provider_list = prioritize_providers(info.get("providers", {}).items())
        timeout = timeout_s if timeout_s is not None else self.provider_timeout_s
        self._prune_failure_cache()

        async def _fetch_one(pid: str, psymbol: str) -> Optional[Dict[str, Any]]:
            provider = self.providers.get(pid)
            if provider is None:
                return None
            cache_key = f"cross:{pid}:{psymbol}"
            if self._in_cooldown(cache_key):
                return None

            start = time.time()
            try:
                quote = await asyncio.wait_for(provider.get_quote(psymbol), timeout=timeout)
            except Exception:
                self._record_failure(cache_key)
                return None
            if not isinstance(quote, TickerModel):
                self._record_failure(cache_key)
                return None

            self._record_success(cache_key)
            elapsed_ms = (time.time() - start) * 1000.0
            received_at = int(time.time() * 1000)
            d = quote.model_dump()
            d["provider"] = pid
            d["latency_ms"] = round(elapsed_ms, 2)
            d["received_at"] = received_at
            ts = d.get("timestamp")
            d["age_ms"] = round(max(0.0, float(received_at - ts)), 2) if ts else 0.0
            return d

        tasks = [_fetch_one(pid, psymbol) for pid, psymbol in provider_list]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def get_health(self) -> List[Dict[str, Any]]:
        async def check(provider_id: str, provider: MarketDataProvider) -> Dict[str, Any]:
            try:
                result = await asyncio.wait_for(
                    provider.health_check(), timeout=self.provider_timeout_s,
                )
                if isinstance(result, dict):
                    return result
                return {"provider": provider_id, "status": "ERROR",
                        "error": "invalid health response"}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return {"provider": provider_id, "status": "OFFLINE", "error": str(exc)}

        return await asyncio.gather(*(
            check(provider_id, provider)
            for provider_id, provider in self.providers.items()
        ))
