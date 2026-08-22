"""
Reusable offline mocks for the test suite (LOT G).

Full orderbook / trades / ticker / cross-quote / provider / engine fakes so
critical engines can be covered without any network access.
"""
from typing import Any, Dict, List, Optional
import time

import pandas as pd

from api.engines.data_providers.base_provider import TickerModel
from api.engines.market_universe import MarketUniverse


# --------------------------------------------------------------------------- #
# Market data builders                                                        #
# --------------------------------------------------------------------------- #
def build_orderbook(bids: Optional[List[list]] = None,
                    asks: Optional[List[list]] = None) -> Dict[str, Any]:
    return {
        "bids": bids if bids is not None else [[100.0, 10.0], [99.9, 10.0]],
        "asks": asks if asks is not None else [[100.1, 10.0], [100.2, 10.0]],
    }


def build_trades(n_buys: int = 5, n_sells: int = 2,
                 amount: float = 1.0) -> List[Dict[str, Any]]:
    trades = [{"side": "buy", "amount": amount, "price": 100.0} for _ in range(n_buys)]
    trades += [{"side": "sell", "amount": amount, "price": 100.0} for _ in range(n_sells)]
    return trades


def build_ticker(symbol: str = "BTC/USDT", last: float = 100.0,
                 bid: Optional[float] = None, ask: Optional[float] = None,
                 status: str = "LIVE", age_ms: int = 500) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "display_symbol": symbol,
        "last": last,
        "bid": bid if bid is not None else last * 0.999,
        "ask": ask if ask is not None else last * 1.001,
        "spread": 0.2,
        "volume": 1000.0,
        "change_24h": 1.0,
        "status": status,
        "source": "test",
        "timestamp": int(time.time() * 1000) - age_ms,
    }


def build_quote(provider: str, last: float, age_ms: int = 500,
                latency_ms: float = 10.0) -> Dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "provider": provider,
        "last": last,
        "timestamp": now - age_ms,
        "received_at": now,
        "age_ms": float(age_ms),
        "latency_ms": latency_ms,
    }


def build_ohlcv(closes: Optional[List[float]] = None,
                n: int = 30, base: float = 100.0,
                bar_range: float = 0.5) -> pd.DataFrame:
    """Deterministic trending OHLCV (uptrend) with High/Low/Close/Volume/Timestamp."""
    if closes is None:
        closes = [base + i * 0.5 for i in range(n)]
    highs = [c + bar_range for c in closes]
    lows = [c - bar_range for c in closes]
    volumes = [100.0 + i for i in range(len(closes))]
    return pd.DataFrame({'Timestamp': range(len(closes)), 'Close': closes,
                         'High': highs, 'Low': lows, 'Volume': volumes})


def ticker_model(symbol: str = "BTC/USDT", last: float = 100.0,
                 age_ms: int = 500) -> TickerModel:
    return TickerModel(
        symbol=symbol, asset_class="CRYPTO", exchange="test",
        timestamp=int(time.time() * 1000) - age_ms, last=last,
        bid=last * 0.999, ask=last * 1.001, source="test", status="LIVE",
    )


# --------------------------------------------------------------------------- #
# Fake ccxt exchange (for provider unit tests)                                #
# --------------------------------------------------------------------------- #
class FakeExchange:
    """Async stand-in for a ccxt exchange instance (offline)."""

    def __init__(self, ticker: Optional[Dict[str, Any]] = None,
                 ohlcv: Optional[List[list]] = None,
                 orderbook: Optional[Dict[str, Any]] = None,
                 trades: Optional[List[Dict[str, Any]]] = None):
        self.ticker = ticker or {"last": 100.0, "bid": 99.9, "ask": 100.1,
                                 "timestamp": int(time.time() * 1000),
                                 "baseVolume": 1000.0, "percentage": 1.0}
        self.ohlcv = ohlcv or [[int(time.time() * 1000), 100.0, 101.0, 99.0, 100.5, 10.0] for _ in range(10)]
        self.orderbook = orderbook or {"bids": [[100.0, 1.0]], "asks": [[100.1, 1.0]]}
        self.trades = trades or [{"side": "buy", "amount": 1.0}]
        self.closed = False

    async def fetch_ticker(self, symbol):
        return dict(self.ticker)

    async def fetch_ohlcv(self, symbol, timeframe="1m", limit=100):
        return list(self.ohlcv)

    async def fetch_order_book(self, symbol, limit=20):
        return dict(self.orderbook)

    async def fetch_trades(self, symbol, limit=50):
        return list(self.trades)

    async def load_markets(self):
        return {}

    async def close(self):
        self.closed = True


class FailingExchange(FakeExchange):
    async def fetch_ticker(self, symbol):
        raise RuntimeError("exchange down")


# --------------------------------------------------------------------------- #
# Fake engines (scanner offline coverage)                                     #
# --------------------------------------------------------------------------- #
class FakeNewsEngine:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed
        self.calls = 0

    async def check_trading_allowed(self, asset_currency=None, asset_class=None):
        self.calls += 1
        return {
            "trading_allowed": self.allowed,
            "day_ok": True, "session_ok": True, "news_ok": self.allowed,
            "blocking_event": None if self.allowed else {"title": "Fake news block"},
            "next_events": [], "status": "OK" if self.allowed else "BLOCKED",
        }


class FakeDataEngine:
    """Offline DataEngine stand-in used to exercise ScannerEngine paths."""

    def __init__(self, universe: Optional[MarketUniverse] = None):
        self.universe = universe or MarketUniverse()
        self.layer = None
        self.ohlcv = build_ohlcv()
        self.ticker = build_ticker()
        self.orderbook = build_orderbook()
        self.trades = build_trades()
        self.cross_quotes = [build_quote("gate", 100.0), build_quote("bybit", 100.1)]

    def is_realtime_capable(self, market_id: str) -> bool:
        info = self.universe.get_info(market_id)
        return bool(info and info.get("asset_class") == "CRYPTO")

    def is_fresh(self, ticker: Dict[str, Any], asset_class: str) -> bool:
        return True

    async def fetch_ohlcv(self, market_id, timeframe="1m", limit=100):
        return self.ohlcv.copy()

    async def fetch_ticker(self, market_id):
        return dict(self.ticker)

    async def fetch_order_book(self, market_id):
        return dict(self.orderbook)

    async def fetch_trades(self, market_id):
        return list(self.trades)

    async def fetch_cross_quotes(self, market_id):
        return [dict(q) for q in self.cross_quotes]
