"""Offline unit tests for PublicCCXTProvider."""
import pytest

from api.engines.data_providers.public_ccxt_provider import PublicCCXTProvider


class FakeExchange:
    def __init__(self, fail=False):
        self.fail = fail

    async def load_markets(self):
        if self.fail:
            raise RuntimeError("down")
        return {"BTC/USDT": {}, "ETH/USD": {}, "EUR/GBP": {}}

    async def fetch_ticker(self, symbol):
        if self.fail:
            raise RuntimeError("down")
        if symbol == "EMPTY":
            return {"last": None}
        return {
            "last": 100.0,
            "close": 100.0,
            "bid": 99.5,
            "ask": 100.5,
            "timestamp": 1,
            "baseVolume": 10,
            "percentage": 1.2,
        }

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        if self.fail:
            raise RuntimeError("down")
        return [[1, 1, 2, 0.5, 1.5, 10]]

    async def fetch_order_book(self, symbol, limit=20):
        if self.fail:
            raise RuntimeError("down")
        return {"bids": [[99, 1]], "asks": [[101, 1]]}

    async def fetch_trades(self, symbol, limit=50):
        if self.fail:
            raise RuntimeError("down")
        return [{"id": 1}]

    async def close(self):
        self.closed = True


async def test_public_ccxt_happy_path():
    p = PublicCCXTProvider(FakeExchange(), "gate")
    symbols = await p.get_symbols()
    assert "BTC/USDT" in symbols and "ETH/USD" in symbols
    assert "EUR/GBP" not in symbols

    q = await p.get_quote("BTC/USDT")
    assert q is not None and q.spread == pytest.approx(1.0)
    assert await p.get_quote("EMPTY") is None

    df = await p.get_ohlcv("BTC/USDT")
    assert not df.empty
    assert (await p.get_order_book("BTC/USDT"))["bids"]
    assert await p.get_recent_trades("BTC/USDT")
    health = await p.health_check()
    assert health["status"] == "ONLINE"
    await p.close()


async def test_public_ccxt_errors():
    p = PublicCCXTProvider(FakeExchange(fail=True), "gate")
    assert await p.get_symbols() == []
    assert await p.get_quote("BTC/USDT") is None
    assert (await p.get_ohlcv("BTC/USDT")).empty
    assert await p.get_order_book("BTC/USDT") is None
    assert await p.get_recent_trades("BTC/USDT") is None
    health = await p.health_check()
    assert health["status"] == "ERROR"


async def test_health_falls_back_to_btc_usd():
    class Partial(FakeExchange):
        async def fetch_ticker(self, symbol):
            if symbol == "BTC/USDT":
                return {"last": None}
            return await super().fetch_ticker(symbol)

    p = PublicCCXTProvider(Partial(), "kraken")
    health = await p.health_check()
    assert health["status"] == "ONLINE"
