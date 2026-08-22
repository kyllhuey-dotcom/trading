"""
LOT B — Robustesse arbitrage micro-temporel.

Covers:
- MicroArbitrageStrategy: freshness gate, synchronization gate, confidence
  scoring, backward compatibility with timing-less quotes;
- DataLayer.get_cross_quotes: strict per-provider timeout, timing metadata
  (latency_ms / received_at / age_ms), failure cooldown.
"""
import asyncio
import time

import pandas as pd
import pytest

from api.engines.data_layer import DataLayer
from api.engines.data_providers.base_provider import TickerModel
from api.engines.strategies.micro_arbitrage import MicroArbitrageStrategy

NOW_MS = int(time.time() * 1000)


def _df():
    return pd.DataFrame({'Close': [100] * 20})


def _mk_quote(provider: str, last: float, age_ms: float = 0.0,
              latency_ms: float = 0.0, received_at: int = NOW_MS,
              timestamp: int = NOW_MS):
    return {
        "last": last,
        "provider": provider,
        "timestamp": timestamp,
        "age_ms": age_ms,
        "latency_ms": latency_ms,
        "received_at": received_at,
    }


# --------------------------------------------------------------------------- #
# 1. Strategy: spread detection still works (fresh + synchronized)            #
# --------------------------------------------------------------------------- #
def test_fresh_synced_quotes_produce_signal():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    quotes = [
        _mk_quote("gate", 100.0, age_ms=200),
        _mk_quote("bybit", 100.2, age_ms=300),
        _mk_quote("binance", 100.3, age_ms=250),
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["score"] >= 80
    assert 0 <= res["confidence"] <= 100
    assert "Arbitrage" in res["reason"]
    meta = res["metadata"]
    assert meta["spread_pct"] == pytest.approx(0.3, abs=0.001)
    assert meta["avg_age_ms"] == pytest.approx(250, abs=1)
    assert meta["dispersion_ms"] == 0
    assert meta["stale_quotes"] == 0
    assert len(meta["per_provider"]) == 3


def test_sell_signal_when_primary_is_expensive():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    quotes = [
        _mk_quote("gate", 100.3),
        _mk_quote("bybit", 100.0),
        _mk_quote("binance", 100.1),
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "SELL"
    assert res["score"] >= 80


# --------------------------------------------------------------------------- #
# 2. Freshness gate: stale quotes are dropped / blocked                        #
# --------------------------------------------------------------------------- #
def test_stale_quotes_blocked():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15, max_quote_age_ms=3000)
    quotes = [
        _mk_quote("gate", 100.0, age_ms=100),
        _mk_quote("bybit", 100.3, age_ms=5000),  # stale → dropped
        _mk_quote("binance", 100.0, age_ms=9000),  # stale → dropped
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "NO_TRADE"
    assert "Stale" in res["reason"]
    assert res["score"] == 0


def test_signal_uses_only_fresh_quotes():
    """One stale provider among 3: the trade still stands on the 2 fresh ones."""
    strategy = MicroArbitrageStrategy(threshold_pct=0.15, max_quote_age_ms=3000)
    quotes = [
        _mk_quote("gate", 100.0, age_ms=100),
        _mk_quote("bybit", 100.3, age_ms=200),
        _mk_quote("binance", 100.3, age_ms=8000),  # stale → ignored
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["metadata"]["stale_quotes"] == 1
    assert res["metadata"]["providers"] == ["gate", "bybit"]


# --------------------------------------------------------------------------- #
# 3. Synchronization gate: quotes too far apart in time are rejected          #
# --------------------------------------------------------------------------- #
def test_unsynchronized_quotes_blocked():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15, max_sync_dispersion_ms=2000)
    quotes = [
        _mk_quote("gate", 100.0, received_at=NOW_MS),
        _mk_quote("bybit", 100.3, received_at=NOW_MS + 5000),  # 5 s apart
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "NO_TRADE"
    assert "not synchronized" in res["reason"]
    assert res["metadata"]["dispersion_ms"] == 5000


def test_dispersion_within_tolerance_trades_with_penalty():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15, max_sync_dispersion_ms=4000)
    quotes = [
        _mk_quote("gate", 100.0, received_at=NOW_MS),
        _mk_quote("bybit", 100.3, received_at=NOW_MS + 2000),
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "SIGNAL_DETECTED"
    # sync_factor = 1 - 2000/4000 = 0.5 → confidence = base * (0.6 + 0.4*0.5)
    expected = 100 * (0.6 + 0.4 * 0.5)
    assert res["confidence"] == pytest.approx(expected, abs=0.1)


# --------------------------------------------------------------------------- #
# 4. Confidence scoring                                                       #
# --------------------------------------------------------------------------- #
def test_confidence_penalized_by_age():
    fresh = MicroArbitrageStrategy(threshold_pct=0.15, max_quote_age_ms=3000)
    quotes_fresh = [_mk_quote("gate", 100.0, age_ms=0), _mk_quote("bybit", 100.3, age_ms=0)]
    res_fresh = fresh.generate_signal("btc_usdt", _df(), cross_quotes=quotes_fresh)
    assert res_fresh["confidence"] == 100

    aged = MicroArbitrageStrategy(threshold_pct=0.15, max_quote_age_ms=3000)
    quotes_aged = [_mk_quote("gate", 100.0, age_ms=1500), _mk_quote("bybit", 100.3, age_ms=1500)]
    res_aged = aged.generate_signal("btc_usdt", _df(), cross_quotes=quotes_aged)
    # age_factor = 0.5 → confidence = 100 * (0.6 + 0.4*0.5) = 80
    assert res_aged["confidence"] == pytest.approx(80, abs=0.1)
    assert res_aged["score"] < res_fresh["score"] or res_aged["score"] <= 100
    assert 0 <= res_aged["confidence"] <= 100


def test_min_confidence_gate():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15, max_quote_age_ms=3000,
                                      min_confidence=90)
    quotes_aged = [_mk_quote("gate", 100.0, age_ms=1500), _mk_quote("bybit", 100.3, age_ms=1500)]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes_aged)
    assert res["status"] == "NO_TRADE"
    assert "Confidence too low" in res["reason"]


# --------------------------------------------------------------------------- #
# 5. Backward compatibility (quotes without timing info)                      #
# --------------------------------------------------------------------------- #
def test_quotes_without_timing_info_still_trade():
    """Legacy callers passing bare {last, provider} dicts keep the old behavior."""
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    quotes = [
        {"last": 100.0, "provider": "gate"},
        {"last": 100.2, "provider": "bybit"},
        {"last": 100.3, "provider": "binance"},
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["score"] >= 80
    assert res["confidence"] == 100


def test_spread_too_low_reason_preserved():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    quotes = [
        {"last": 100.0, "provider": "gate"},
        {"last": 100.1, "provider": "bybit"},
    ]
    res = strategy.generate_signal("btc_usdt", _df(), cross_quotes=quotes)
    assert res["status"] == "NO_TRADE"
    assert "Spread too low" in res["reason"]


# --------------------------------------------------------------------------- #
# 6. DataLayer: strict per-provider timeout + timing metadata                 #
# --------------------------------------------------------------------------- #
class FakeQuoteProvider:
    """Minimal async provider returning a TickerModel."""

    def __init__(self, price: float, delay_s: float = 0.0, fail: bool = False,
                 age_ms: int = 500):
        self.price = price
        self.delay_s = delay_s
        self.fail = fail
        self.age_ms = age_ms
        self.calls = 0

    async def get_quote(self, symbol: str):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return TickerModel(
            symbol=symbol, asset_class="CRYPTO", exchange="test",
            timestamp=int(time.time() * 1000) - self.age_ms,
            last=self.price, source="test", status="LIVE",
        )


class FakeCatalog:
    def __init__(self):
        self.info = {
            "btc_usdt": {"providers": {"gate": "BTC/USDT", "bybit": "BTC/USDT",
                                       "binance": "BTC/USDT"}}
        }

    def get_info(self, market_id):
        return self.info.get(market_id)


async def test_cross_quotes_attach_timing_metadata():
    layer = DataLayer()
    gate = FakeQuoteProvider(price=100.0, age_ms=1200)
    bybit = FakeQuoteProvider(price=100.3, age_ms=800)
    layer.register_provider("gate", gate)
    layer.register_provider("bybit", bybit)

    quotes = await layer.get_cross_quotes("btc_usdt", FakeCatalog())
    assert len(quotes) == 2
    by_provider = {q["provider"]: q for q in quotes}
    assert set(by_provider) == {"gate", "bybit"}
    for q in quotes:
        assert q["latency_ms"] >= 0
        assert "received_at" in q
        assert q["age_ms"] == pytest.approx(q["received_at"] - q["timestamp"], abs=2)
    assert by_provider["gate"]["age_ms"] == pytest.approx(1200, abs=200)


async def test_cross_quotes_strict_timeout_drops_slow_provider():
    layer = DataLayer()
    layer.provider_timeout_s = 0.2
    fast = FakeQuoteProvider(price=100.0)
    slow = FakeQuoteProvider(price=100.5, delay_s=5.0)
    layer.register_provider("gate", fast)
    layer.register_provider("bybit", slow)

    start = time.time()
    quotes = await layer.get_cross_quotes("btc_usdt", FakeCatalog())
    elapsed = time.time() - start

    assert elapsed < 3.0  # did not wait for the slow provider
    assert [q["provider"] for q in quotes] == ["gate"]


async def test_cross_quotes_failure_goes_to_cooldown():
    layer = DataLayer()
    layer.failure_cooldown = 3600
    good = FakeQuoteProvider(price=100.0)
    bad = FakeQuoteProvider(price=100.5, fail=True)
    layer.register_provider("gate", good)
    layer.register_provider("bybit", bad)

    quotes = await layer.get_cross_quotes("btc_usdt", FakeCatalog())
    assert [q["provider"] for q in quotes] == ["gate"]
    # bybit is in the failure cache and won't be retried until cooldown ends
    assert "cross:bybit:BTC/USDT" in layer.failure_cache

    # Second call skips bybit immediately (still within cooldown)
    quotes2 = await layer.get_cross_quotes("btc_usdt", FakeCatalog())
    assert [q["provider"] for q in quotes2] == ["gate"]
    assert bad.calls == 1  # never retried


async def test_cross_quotes_unknown_market():
    layer = DataLayer()
    assert await layer.get_cross_quotes("nope", FakeCatalog()) == []
