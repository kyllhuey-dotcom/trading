"""Offline unit tests for TwelveData / Finnhub providers."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.engines.data_providers.keyed_tradfi_provider import (
    FinnhubProvider,
    ProviderRateLimiter,
    TwelveDataProvider,
    _float_or_none,
)


def test_float_or_none():
    assert _float_or_none("1.5") == 1.5
    assert _float_or_none(None) is None
    assert _float_or_none("") is None
    assert _float_or_none("x") is None


def test_providers_require_keys():
    with pytest.raises(ValueError):
        TwelveDataProvider("")
    with pytest.raises(ValueError):
        FinnhubProvider("")


async def test_rate_limiter_waits(monkeypatch):
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    limiter = ProviderRateLimiter(requests_per_minute=60)
    limiter._last_request = 0.0
    await limiter.wait()
    await limiter.wait()
    assert sleeps  # second call should delay


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return _Resp(self.payload)


async def test_twelvedata_quote_ohlcv_health():
    td = TwelveDataProvider("k", requests_per_minute=10_000)
    payload = {
        "close": "190.0",
        "price": "190.0",
        "name": "Apple",
        "exchange": "NASDAQ",
        "timestamp": "1700000000",
        "open": "189",
        "high": "191",
        "low": "188",
        "volume": "1000",
        "percent_change": "1.2",
    }
    with patch("api.engines.data_providers.keyed_tradfi_provider.httpx.AsyncClient",
               return_value=_Client(payload)):
        q = await td.get_quote("AAPL")
        assert q is not None
        assert q.last == 190.0
        assert q.status == "LIVE"

    series = {
        "values": [
            {"datetime": "2024-01-02 00:00:00", "open": "1", "high": "2",
             "low": "0.5", "close": "1.5", "volume": "10"},
        ]
    }
    with patch("api.engines.data_providers.keyed_tradfi_provider.httpx.AsyncClient",
               return_value=_Client(series)):
        df = await td.get_ohlcv("AAPL", "1h", 10)
        assert not df.empty
        assert list(df.columns) == ["Timestamp", "Open", "High", "Low", "Close", "Volume"]

    with patch.object(td, "get_quote", AsyncMock(return_value=q)):
        health = await td.health_check()
        assert health["status"] == "ONLINE"

    assert await td.get_symbols() == []

    with patch.object(td, "_get", AsyncMock(return_value={"status": "error"})):
        assert await td.get_quote("AAPL") is None

    with patch.object(td, "_get", AsyncMock(side_effect=RuntimeError("net"))):
        assert await td.get_quote("AAPL") is None
        assert (await td.get_ohlcv("AAPL")).empty


async def test_finnhub_quote_ohlcv_health():
    fh = FinnhubProvider("k", requests_per_minute=10_000)
    quote = {"c": 100.0, "t": 1700000000, "o": 99, "h": 101, "l": 98, "dp": 1.1}
    with patch("api.engines.data_providers.keyed_tradfi_provider.httpx.AsyncClient",
               return_value=_Client(quote)):
        q = await fh.get_quote("AAPL")
        assert q is not None and q.last == 100.0

    candles = {
        "s": "ok",
        "t": [1700000000],
        "o": [1], "h": [2], "l": [0.5], "c": [1.5], "v": [10],
    }
    with patch("api.engines.data_providers.keyed_tradfi_provider.httpx.AsyncClient",
               return_value=_Client(candles)):
        df = await fh.get_ohlcv("AAPL", "1d", 5)
        assert not df.empty

    with patch.object(fh, "get_quote", AsyncMock(return_value=q)):
        health = await fh.health_check()
        assert health["status"] == "ONLINE"

    assert await fh.get_symbols() == []

    with patch.object(fh, "_get", AsyncMock(return_value={"c": 0})):
        assert await fh.get_quote("AAPL") is None
    with patch.object(fh, "_get", AsyncMock(return_value={"s": "no_data"})):
        assert (await fh.get_ohlcv("AAPL")).empty
    with patch.object(fh, "_get", AsyncMock(side_effect=RuntimeError("x"))):
        assert await fh.get_quote("AAPL") is None
        assert (await fh.get_ohlcv("AAPL")).empty
