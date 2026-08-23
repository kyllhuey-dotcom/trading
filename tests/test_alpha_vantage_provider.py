"""v2.8 — Alpha Vantage tradfi provider tests (all network mocked).

Covers:
- quote parsing (equity GLOBAL_QUOTE, forex CURRENCY_EXCHANGE_RATE)
- OHLCV parsing (TIME_SERIES_INTRADAY / FX_INTRADAY)
- 5-calls/minute rate limiting and 25-calls/day client-side quota
- graceful fallback to Yahoo when the quota is exhausted (None / empty)
- data-engine wiring (priority before Yahoo, env-key activation)
"""
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from api.engines.data_engine import DataEngine
from api.engines.data_providers.alpha_vantage_provider import AlphaVantageProvider
from api.engines.provider_priority import PRIORITY, prioritize_providers


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """httpx.AsyncClient stub returning a queued payload."""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, timeout=None):
        return _FakeResponse(self._payload)


EQUITY_QUOTE = {
    "Global Quote": {
        "01. symbol": "AAPL",
        "02. open": "229.50",
        "03. high": "231.20",
        "04. low": "228.90",
        "05. price": "230.75",
        "06. volume": "12345678",
        "10. change percent": "0.62%",
    }
}

FOREX_QUOTE = {
    "Realtime Currency Exchange Rate": {
        "1. From_Currency Code": "EUR",
        "3. To_Currency Code": "USD",
        "5. Exchange Rate": "1.0850",
        "8. Bid Price": "1.0849",
        "9. Ask Price": "1.0851",
    }
}

STOCK_SERIES = {
    "Time Series (1min)": {
        "2026-08-21 16:01:00": {"1. open": "231.0", "2. high": "231.5",
                                 "3. low": "230.8", "4. close": "231.2",
                                 "5. volume": "1000"},
        "2026-08-21 16:00:00": {"1. open": "230.5", "2. high": "231.1",
                                 "3. low": "230.4", "4. close": "231.0",
                                 "5. volume": "900"},
    }
}

FX_SERIES = {
    "Time Series FX (5min)": {
        "2026-08-21 16:05:00": {"1. open": "1.085", "2. high": "1.086",
                                 "3. low": "1.084", "4. close": "1.0855"},
        "2026-08-21 16:00:00": {"1. open": "1.084", "2. high": "1.0855",
                                 "3. low": "1.083", "4. close": "1.085"},
    }
}

QUOTA_NOTE = {"Note": "Thank you for using Alpha Vantage! Our standard API rate "
                      "limit is 25 requests per day."}


def _provider(monkeypatch, payload):
    provider = AlphaVantageProvider("TEST_KEY", requests_per_minute=10_000, daily_quota=25)
    monkeypatch.setattr("api.engines.data_providers.alpha_vantage_provider.httpx.AsyncClient",
                        lambda *a, **k: _FakeClient(payload))
    return provider


# --------------------------------------------------------------------------- #
# Symbol mapping                                                              #
# --------------------------------------------------------------------------- #
def test_forex_pair_parsing():
    assert AlphaVantageProvider.forex_pair("EURUSD=X") == ("EUR", "USD")
    assert AlphaVantageProvider.forex_pair("gbpjpy=x".upper()) == ("GBP", "JPY")
    assert AlphaVantageProvider.forex_pair("EUR/USD") == ("EUR", "USD")
    assert AlphaVantageProvider.forex_pair("AAPL") is None


def test_requires_api_key():
    with pytest.raises(ValueError):
        AlphaVantageProvider("")


# --------------------------------------------------------------------------- #
# Quotes                                                                      #
# --------------------------------------------------------------------------- #
async def test_equity_quote_parsing(monkeypatch):
    provider = _provider(monkeypatch, EQUITY_QUOTE)
    quote = await provider.get_quote("AAPL")
    assert quote is not None
    assert quote.last == 230.75
    assert quote.open == 229.50
    assert quote.high == 231.20
    assert quote.low == 228.90
    assert quote.volume == 12345678
    assert quote.change_24h == 0.62
    assert quote.source == "AlphaVantage"
    assert quote.status == "LIVE"


async def test_forex_quote_parsing(monkeypatch):
    provider = _provider(monkeypatch, FOREX_QUOTE)
    quote = await provider.get_quote("EURUSD=X")
    assert quote is not None
    assert quote.asset_class == "FOREX"
    assert quote.last == 1.0850
    assert quote.bid == 1.0849
    assert quote.ask == 1.0851
    assert quote.spread == pytest.approx(0.0002)
    assert quote.status == "LIVE"


async def test_equity_quote_empty_payload(monkeypatch):
    provider = _provider(monkeypatch, {"Global Quote": {}})
    assert await provider.get_quote("AAPL") is None


async def test_http_error_returns_none(monkeypatch):
    provider = AlphaVantageProvider("TEST_KEY", requests_per_minute=10_000)

    class _Down:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr("api.engines.data_providers.alpha_vantage_provider.httpx.AsyncClient",
                        lambda *a, **k: _Down())
    assert await provider.get_quote("AAPL") is None


# --------------------------------------------------------------------------- #
# OHLCV                                                                       #
# --------------------------------------------------------------------------- #
async def test_equity_ohlcv_parsing_oldest_first(monkeypatch):
    provider = _provider(monkeypatch, STOCK_SERIES)
    df = await provider.get_ohlcv("AAPL", "1m", 100)
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 2
    # oldest candle first (Alpha Vantage sends most-recent-first)
    assert df.iloc[0]["Open"] == 230.5
    assert df.iloc[1]["Close"] == 231.2
    assert df.iloc[0]["Timestamp"] < df.iloc[1]["Timestamp"]


async def test_forex_ohlcv_parsing_zero_volume(monkeypatch):
    provider = _provider(monkeypatch, FX_SERIES)
    df = await provider.get_ohlcv("EURUSD=X", "5m", 100)
    assert len(df) == 2
    assert (df["Volume"] == 0).all()
    assert df.iloc[0]["Open"] == 1.084


# --------------------------------------------------------------------------- #
# Rate limiting & daily quota                                                 #
# --------------------------------------------------------------------------- #
async def test_rate_limiter_respected(monkeypatch):
    # Min interval between calls must be 60/rpm seconds.
    provider = AlphaVantageProvider("TEST_KEY", requests_per_minute=5)
    assert provider.rate_limiter.min_interval_s == pytest.approx(12.0)

    sleeps = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(
        "api.engines.data_providers.keyed_tradfi_provider.asyncio.sleep", _fake_sleep)
    await provider.rate_limiter.wait()
    await provider.rate_limiter.wait()
    # second call had to wait ~the full interval
    assert sleeps and sleeps[-1] > 0
    assert sleeps[-1] <= 12.0


async def test_daily_quota_exhaustion_falls_back(monkeypatch):
    """Once the 25-call free quota is spent the provider refuses calls and the
    data layer falls back to the next provider (Yahoo) — no exception escapes."""
    provider = _provider(monkeypatch, EQUITY_QUOTE)
    provider.daily_quota = 3
    assert await provider.get_quote("AAPL") is not None
    assert await provider.get_quote("AAPL") is not None
    assert await provider.get_quote("AAPL") is not None
    assert provider.daily_calls_remaining == 0
    # quota exhausted: returns None without any further HTTP call
    assert await provider.get_quote("AAPL") is None
    df = await provider.get_ohlcv("AAPL", "1m", 10)
    assert isinstance(df, pd.DataFrame) and df.empty


async def test_quota_note_payload_trips_breaker(monkeypatch):
    """Alpha Vantage answers quota breaches with HTTP 200 + a Note payload."""
    provider = _provider(monkeypatch, QUOTA_NOTE)
    assert await provider.get_quote("AAPL") is None
    # breaker tripped: subsequent calls are refused locally
    assert provider.daily_calls_remaining == 0
    assert await provider.get_quote("MSFT") is None


async def test_daily_counter_rolls_over(monkeypatch):
    provider = _provider(monkeypatch, EQUITY_QUOTE)
    provider.daily_quota = 1
    assert await provider.get_quote("AAPL") is not None
    assert provider.daily_calls_remaining == 0
    provider._usage_day = "2000-01-01"  # pretend the day changed
    assert provider.daily_calls_remaining == 1
    assert await provider.get_quote("AAPL") is not None


# --------------------------------------------------------------------------- #
# Data-engine wiring                                                          #
# --------------------------------------------------------------------------- #
def test_priority_order_alpha_before_yahoo():
    assert PRIORITY["alpha_vantage"] < PRIORITY["twelvedata"] < PRIORITY["finnhub"]
    ordered = [pid for pid, _ in prioritize_providers(
        [("yahoo_forex", "EURUSD=X"), ("alpha_vantage", "EURUSD=X"),
         ("binance", "BTC/USDT"), ("twelvedata", "EUR/USD")])]
    assert ordered == ["binance", "alpha_vantage", "twelvedata", "yahoo_forex"]


def test_engine_activates_provider_with_env_key(monkeypatch):
    # crypto providers are untouched by the alpha vantage key
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "TEST_KEY_123")
    for var in ("TWELVEDATA_API_KEY", "FINNHUB_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    engine = DataEngine()
    assert engine.alpha_vantage_provider is not None
    assert "alpha_vantage" in engine.layer.providers
    assert "alpha_vantage" in engine.REALTIME_PROVIDERS
    # forex markets get an alpha_vantage mapping (yahoo kept as fallback)
    info = engine.universe.get_info("eur_usd")
    assert info["providers"].get("alpha_vantage") == "EURUSD=X"
    assert "yahoo_forex" in info["providers"]


def test_engine_without_key_keeps_yahoo_only(monkeypatch):
    for var in ("ALPHA_VANTAGE_API_KEY", "TWELVEDATA_API_KEY", "FINNHUB_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    engine = DataEngine()
    assert engine.alpha_vantage_provider is None
    assert "alpha_vantage" not in engine.layer.providers
    info = engine.universe.get_info("eur_usd")
    assert "alpha_vantage" not in info["providers"]


async def test_fallback_to_yahoo_when_quota_exceeded(monkeypatch):
    """DataLayer must serve Yahoo's quote after Alpha Vantage refuses (quota)."""
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "TEST_KEY_123")
    engine = DataEngine()

    exhausted = AsyncMock(return_value=None)
    monkeypatch.setattr(engine.alpha_vantage_provider, "get_quote", exhausted)

    from api.engines.data_providers.base_provider import TickerModel
    yahoo_quote = TickerModel(
        symbol="EURUSD=X", name="EUR/USD", asset_class="FOREX",
        exchange="Yahoo", timestamp=1, last=1.08,
        source="Yahoo Finance", status="DELAYED",
    )
    yahoo_mock = AsyncMock(return_value=yahoo_quote)
    monkeypatch.setattr(engine.yahoo_providers["yahoo_forex"], "get_quote", yahoo_mock)

    quote = await engine.fetch_ticker("eur_usd")
    assert quote is not None
    assert quote["source"] == "Yahoo Finance"
    exhausted.assert_awaited()
    yahoo_mock.assert_awaited()
