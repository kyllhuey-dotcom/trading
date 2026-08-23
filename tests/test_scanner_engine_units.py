"""Offline unit coverage for ScannerEngine (mocks only, no network)."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from api.engines.scanner_engine import ScannerEngine
from tests.mocks import FakeDataEngine, FakeNewsEngine, MarketUniverse


def _small_universe():
    u = MarketUniverse()
    u.universe = {
        "btc_usdt": u.universe["btc_usdt"],
        "eth_usdt": u.universe["eth_usdt"],
        "eur_usd": u.universe["eur_usd"],
    }
    return u


def _scanner(data=None, analysis=None, signal=None, news=None):
    data = data or FakeDataEngine(_small_universe())
    if analysis is None:
        analysis = MagicMock()
        analysis.identify_structure = MagicMock(
            return_value={"trend": "BULLISH", "market_state": "TREND"})
    if signal is None:
        signal = MagicMock()
        signal.generate_signal = MagicMock(return_value={
            "status": "SIGNAL_DETECTED",
            "strategy": "rsi",
            "score": 90,
            "direction": "BUY",
            "entry": 100,
            "sl": 95,
            "tp": 110,
            "tradable": True,
            "reason": "RSI oversold bounce",
        })
    news = news or FakeNewsEngine(allowed=True)
    return ScannerEngine(data, analysis, signal, news, max_concurrent=2)


@pytest.mark.asyncio
async def test_scan_asset_rsi_happy_path_live():
    scanner = _scanner()
    res = await scanner.scan_asset("btc_usdt", asyncio.Semaphore(1))
    assert res["signal"] == "SIGNAL_DETECTED"
    assert res["status"] == "LIVE"
    assert res["tradable"] is True
    assert res["strategy"] == "rsi"
    assert res["signal_data"]["status"] == "SIGNAL_DETECTED"


@pytest.mark.asyncio
async def test_scan_asset_calendar_timeout():
    news = FakeNewsEngine(allowed=True)
    news.unavailable_status = MagicMock(return_value={
        "trading_allowed": False, "day_ok": True, "session_ok": True,
        "news_ok": False, "status": "DATA_UNAVAILABLE",
        "blocking_event": {"title": "Calendar timeout"}, "next_events": [],
    })

    async def boom(**_k):
        raise asyncio.TimeoutError()

    news.check_trading_allowed = boom
    scanner = _scanner(news=news)
    res = await scanner.scan_asset("btc_usdt", asyncio.Semaphore(1))
    assert res["block_reason"] == "CALENDAR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_scan_asset_structure_raises_continues():
    analysis = MagicMock()
    analysis.identify_structure.side_effect = RuntimeError("structure boom")
    scanner = _scanner(analysis=analysis)
    res = await scanner.scan_asset("btc_usdt", asyncio.Semaphore(1))
    assert res["signal"] == "SIGNAL_DETECTED"
    assert res["trend"] == "NEUTRAL"


@pytest.mark.asyncio
async def test_safe_fetch_quota_error():
    scanner = _scanner()

    async def quota():
        raise RuntimeError("rate limit exceeded — too many requests")

    out = await scanner._safe_fetch(quota(), default=None, label="ticker", symbol="btc_usdt")
    assert out is None


@pytest.mark.asyncio
async def test_scan_all_progress_sync_and_async():
    scanner = _scanner()
    sync_seen = []
    async_seen = []

    def sync_cb(row, done, total):
        sync_seen.append((row["symbol"], done, total))

    async def async_cb(row, done, total):
        async_seen.append((row["symbol"], done, total))

    r1 = await scanner.scan_all(progress_callback=sync_cb)
    assert len(r1) == 3
    assert len(sync_seen) == 3
    assert sync_seen[-1][1:] == (3, 3)

    r2 = await scanner.scan_all(progress_callback=async_cb)
    assert len(r2) == 3
    assert len(async_seen) == 3


@pytest.mark.asyncio
async def test_prepare_scan_cycle_failure_is_swallowed():
    data = FakeDataEngine(_small_universe())

    async def fail(phase):
        raise RuntimeError("prep failed")

    data.prepare_scan_cycle = fail
    scanner = _scanner(data=data)
    results = await scanner.scan_all()
    assert len(results) == 3


@pytest.mark.asyncio
async def test_scan_asset_exception_status_error():
    scanner = _scanner()
    import api.engines.scanner_engine as se
    monkey_orig = se.asyncio.wait_for

    async def patched(coro, timeout=None):
        if timeout == 15.0:
            raise RuntimeError("quota exceeded")
        return await monkey_orig(coro, timeout=timeout)

    se.asyncio.wait_for = patched
    try:
        res = await scanner.scan_asset("btc_usdt", asyncio.Semaphore(1))
    finally:
        se.asyncio.wait_for = monkey_orig
    assert res["status"] == "ERROR"
    assert res["block_reason"] == "PROVIDER_QUOTA_EXCEEDED"
