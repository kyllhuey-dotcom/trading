"""
LOT F — Data & providers hardening.

Covers (all offline, mocked providers):
- DataLayer: per-provider timeouts on quotes/OHLCV/orderbook/trades,
  failure cooldown with exponential escalation, success resets;
- non-realtime source guard (Yahoo delayed data cannot be scalped);
- DataHealthMonitor precision: ONLINE/DEGRADED/SLOW/ERROR normalization,
  consecutive failure tracking.
"""
import asyncio
import time

import pandas as pd
import pytest

from api.engines.data_engine import DataEngine
from api.engines.data_health import DataHealthMonitor
from api.engines.data_layer import DataLayer
from api.engines.data_providers.base_provider import TickerModel
from api.engines.market_universe import MarketUniverse


class SlowProvider:
    def __init__(self, delay_s: float = 0.0, fail: bool = False):
        self.delay_s = delay_s
        self.fail = fail
        self.calls = 0

    async def get_quote(self, symbol):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return TickerModel(symbol=symbol, asset_class="CRYPTO", exchange="test",
                           timestamp=int(time.time() * 1000), last=100.0,
                           source="test", status="LIVE")

    async def get_ohlcv(self, symbol, timeframe, limit=100):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return pd.DataFrame({'Close': [100.0] * 10})

    async def get_order_book(self, symbol):
        self.calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return {'bids': [[100.0, 1]], 'asks': [[100.1, 1]]}

    async def get_recent_trades(self, symbol):
        self.calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return [{'side': 'buy', 'amount': 1}]


# --------------------------------------------------------------------------- #
# 1. Failure cooldown escalation                                              #
# --------------------------------------------------------------------------- #
def test_cooldown_escalates_with_consecutive_failures():
    layer = DataLayer()
    layer.failure_cooldown = 100
    layer.max_failure_cooldown = 1000
    key = "gate:BTC/USDT"
    assert layer._cooldown_for(key) == 100
    layer._record_failure(key)
    assert layer._cooldown_for(key) == 100     # 1st failure: base
    layer._record_failure(key)
    assert layer._cooldown_for(key) == 200     # 2nd: x2
    layer._record_failure(key)
    assert layer._cooldown_for(key) == 400     # 3rd: x4
    for _ in range(10):
        layer._record_failure(key)
    assert layer._cooldown_for(key) == 1000    # capped
    assert layer._in_cooldown(key) is True
    layer._record_success(key)
    assert layer._in_cooldown(key) is False
    assert layer._cooldown_for(key) == 100     # reset


async def test_quote_fallback_and_failure_recorded():
    layer = DataLayer()
    universe = MarketUniverse()
    gate = SlowProvider(fail=True)
    bybit = SlowProvider()
    layer.register_provider("gate", gate)
    layer.register_provider("bybit", bybit)
    quotes = await layer.get_all_quotes(["btc_usdt"], universe)
    assert len(quotes) == 1
    assert "gate:BTC/USDT" in layer.failure_cache  # recorded for cooldown
    assert bybit.calls == 1


# --------------------------------------------------------------------------- #
# 2. Strict per-provider timeouts                                             #
# --------------------------------------------------------------------------- #
async def test_ohlcv_timeout_skips_hung_provider():
    layer = DataLayer()
    layer.provider_timeout_s = 0.2
    universe = MarketUniverse()
    gate = SlowProvider(delay_s=5.0)   # hung
    bybit = SlowProvider()             # healthy
    layer.register_provider("gate", gate)
    layer.register_provider("bybit", bybit)
    df = await layer.get_ohlcv("btc_usdt", "1m", 50, universe)
    assert not df.empty
    assert gate.calls == 1 and bybit.calls == 1
    assert "ohlcv:gate:BTC/USDT" in layer.failure_cache


async def test_orderbook_and_trades_timeouts():
    layer = DataLayer()
    layer.provider_timeout_s = 0.2
    universe = MarketUniverse()
    gate = SlowProvider(delay_s=5.0)
    bybit = SlowProvider()
    layer.register_provider("gate", gate)
    layer.register_provider("bybit", bybit)
    ob = await layer.get_order_book("btc_usdt", universe)
    assert ob is not None and "bids" in ob
    trades = await layer.get_trades("btc_usdt", universe)
    assert trades and trades[0]["side"] == "buy"
    assert "ob:gate:BTC/USDT" in layer.failure_cache
    assert "tr:gate:BTC/USDT" in layer.failure_cache


# --------------------------------------------------------------------------- #
# 3. Non-realtime source guard (Yahoo delayed data)                           #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def data_engine():
    return DataEngine()


def test_realtime_capability_detection(data_engine):
    assert data_engine.is_realtime_capable("btc_usdt") is True   # crypto exchanges
    assert data_engine.is_realtime_capable("eth_usdt") is True
    assert data_engine.is_realtime_capable("eur_usd") is False   # Yahoo source
    assert data_engine.is_realtime_capable("spx") is False
    assert data_engine.is_realtime_capable("__unknown__") is False


def test_scalping_guard_blocks_delayed_sources(data_engine):
    crypto = data_engine.check_scalping_allowed("btc_usdt")
    assert crypto == {"allowed": True, "reason": None, "realtime": True}

    forex = data_engine.check_scalping_allowed("eur_usd")
    assert forex["allowed"] is False
    assert forex["realtime"] is False
    assert "NON_REALTIME_SOURCE" in forex["reason"]

    # Explicit opt-out via settings
    opt_out = data_engine.check_scalping_allowed("eur_usd", allow_delayed=True)
    assert opt_out["allowed"] is True
    assert "explicitly allowed" in opt_out["reason"]


# --------------------------------------------------------------------------- #
# 4. Health monitor precision                                                 #
# --------------------------------------------------------------------------- #
class FakeHealthProvider:
    def __init__(self, result):
        self.result = result

    async def health_check(self):
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)


def test_health_status_normalization():
    assert DataHealthMonitor.normalize_status("ONLINE", 50) == "ONLINE"
    assert DataHealthMonitor.normalize_status("ONLINE", 1500) == "DEGRADED"
    assert DataHealthMonitor.normalize_status("ONLINE", 5000) == "SLOW"
    assert DataHealthMonitor.normalize_status("ERROR", 0) == "ERROR"
    assert DataHealthMonitor.normalize_status(None, 0) == "UNKNOWN"
    assert DataHealthMonitor.normalize_status("OK", 200) == "ONLINE"


async def test_health_report_precision_and_failure_tracking():
    providers = {
        "gate": FakeHealthProvider({"status": "ONLINE", "latency_ms": 50}),
        "bybit": FakeHealthProvider({"status": "ONLINE", "latency_ms": 2500}),
        "binance": FakeHealthProvider(RuntimeError("boom")),
    }
    monitor = DataHealthMonitor(providers)

    report1 = await monitor.get_health_report()
    by_pid = {r["provider_id"]: r for r in report1}
    assert by_pid["gate"]["status"] == "ONLINE"
    assert by_pid["bybit"]["status"] == "DEGRADED"
    assert by_pid["binance"]["status"] == "ERROR"
    assert by_pid["binance"]["consecutive_failures"] == 1
    assert by_pid["gate"]["consecutive_failures"] == 0
    assert by_pid["gate"]["last_ok"] is not None

    report2 = await monitor.get_health_report()
    by_pid2 = {r["provider_id"]: r for r in report2}
    assert by_pid2["binance"]["consecutive_failures"] == 2
    assert by_pid2["gate"]["checks"] == 2


async def test_health_report_isolated_from_one_hung_provider():
    class HungProvider:
        async def health_check(self):
            await asyncio.sleep(30)
            return {"status": "ONLINE"}

    providers = {"gate": FakeHealthProvider({"status": "ONLINE", "latency_ms": 10}),
                 "bybit": HungProvider()}
    monitor = DataHealthMonitor(providers)
    report = await monitor.get_health_report()  # must not take 30 s
    by_pid = {r["provider_id"]: r for r in report}
    assert by_pid["gate"]["status"] == "ONLINE"
    assert by_pid["bybit"]["status"] == "ERROR"  # timeout caught as error
