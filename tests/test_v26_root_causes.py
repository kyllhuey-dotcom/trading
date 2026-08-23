"""Offline regression coverage for the v2.6 production root-cause fixes."""
from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.index as idx
from api.engines.data_engine import DataEngine
from api.engines.data_providers.coinbase_provider import CoinbaseProvider
from api.engines.data_providers.kraken_provider import KrakenProvider
from api.engines.data_providers.okx_provider import OKXProvider
from api.engines.data_providers.yahoo_provider import YahooProvider
from api.engines.db_manager import DatabaseManager
from api.engines.market_hub import enrich_overview
from api.engines.market_universe import MarketUniverse
from api.engines.news_engine import EconomicCalendarProvider, NewsEngine
from api.engines.provider_priority import prioritize_providers
from api.engines.radar import prepare_radar
from api.engines.scanner_engine import ScannerEngine


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _CalendarClient:
    responses = []
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **_kwargs):
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


JSON_EVENT = {
    "title": "CPI m/m", "country": "USD", "date": "2026-08-20T08:30:00-04:00",
    "impact": "High", "forecast": "0.2%", "previous": "0.3%",
}
HTML_EVENT = """
<table class="calendar__table"><tr class="calendar__row">
<td class="calendar__date">Thu Aug 20</td><td class="calendar__time">8:30am</td>
<td class="calendar__currency">USD</td><td class="calendar__impact"><span class="high"></span></td>
<td class="calendar__event">CPI HTML</td><td class="calendar__forecast">0.2%</td>
<td class="calendar__previous">0.3%</td><td class="calendar__actual"></td>
</tr></table>
"""


async def test_calendar_json_first_and_normalized(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "calendar.db"))
    _CalendarClient.calls = []
    _CalendarClient.responses = [_Response(payload=[JSON_EVENT])]
    monkeypatch.setattr("api.engines.news_engine.httpx.AsyncClient", _CalendarClient)
    provider = EconomicCalendarProvider(db)
    events = await provider.fetch_events()
    assert events[0]["title"] == "CPI m/m"
    assert events[0]["timestamp_utc"].endswith("+00:00")
    assert provider.source == "faireconomy_json"
    assert _CalendarClient.calls == [provider.JSON_URL]
    assert db.load_calendar_cache(provider.PERSISTED_TTL_S)["events"]


async def test_calendar_html_fallback_then_seven_day_database_cache(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "calendar.db"))
    _CalendarClient.calls = []
    _CalendarClient.responses = [_Response(503), _Response(text=HTML_EVENT)]
    monkeypatch.setattr("api.engines.news_engine.httpx.AsyncClient", _CalendarClient)
    first = EconomicCalendarProvider(db)
    assert (await first.fetch_events())[0]["title"] == "CPI HTML"
    assert first.status == "FALLBACK"
    assert _CalendarClient.calls == [first.JSON_URL, first.HTML_URL]

    _CalendarClient.responses = [RuntimeError("offline"), RuntimeError("offline")]
    cached = EconomicCalendarProvider(db)
    assert (await cached.fetch_events())[0]["title"] == "CPI HTML"
    assert cached.status == "CACHED"
    assert cached.get_state()["age_s"] <= 2

    db.save_calendar_cache([JSON_EVENT], "old", time.time() - 7 * 86400 - 1)
    _CalendarClient.responses = [RuntimeError("offline"), RuntimeError("offline")]
    expired = EconomicCalendarProvider(db)
    assert await expired.fetch_events() == []
    assert expired.status == "DATA_UNAVAILABLE"


@pytest.mark.parametrize(
    ("policy", "asset_class", "news_ok"),
    [("block_all", "CRYPTO", False),
     ("block_tradfi_only", "CRYPTO", True),
     ("block_tradfi_only", "FOREX", False),
     ("allow_all", "FOREX", True)],
)
async def test_calendar_unavailable_policies(policy, asset_class, news_ok):
    engine = NewsEngine(unavailable_policy=policy)
    engine.provider.fetch_events = AsyncMock(return_value=[])
    result = await engine.check_trading_allowed(asset_class=asset_class)
    assert result["news_ok"] is news_ok
    assert result["unavailable_policy"] == policy
    if not news_ok:
        assert result["trading_allowed"] is False


def _history_frame():
    index = pd.date_range("2026-08-20 09:30", periods=40, freq="1min", tz="UTC")
    return pd.DataFrame({
        "Open": [100.0] * 40, "High": [102.0] * 40, "Low": [99.0] * 40,
        "Close": [100.0 + i / 10 for i in range(40)], "Volume": [10.0] * 40,
    }, index=index)


async def test_yahoo_grouped_batch_and_ttl_cache(monkeypatch):
    calls = []

    class FakeYF:
        @staticmethod
        def download(**kwargs):
            calls.append(kwargs)
            return _history_frame()

    monkeypatch.setattr("api.engines.data_providers.yahoo_provider.yf", FakeYF)
    provider = YahooProvider("STOCKS")
    await provider.prepare_cycle(["AAPL"])
    await provider.prepare_cycle(["AAPL"])
    quote = await provider.get_quote("AAPL")
    one_minute = await provider.get_ohlcv("AAPL", "1m", 20)
    fifteen_minutes = await provider.get_ohlcv("AAPL", "15m", 20)
    assert len(calls) == 1
    assert quote.status == "DELAYED"
    assert len(one_minute) == 20 and not fifteen_minutes.empty


class _FakeExchange:
    async def fetch_ticker(self, symbol):
        return {"timestamp": 1_777_000_000_000, "last": 101, "bid": 100,
                "ask": 102, "baseVolume": 12, "percentage": 1.2}

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        return [[1_777_000_000_000, 100, 102, 99, 101, 12]]

    async def fetch_order_book(self, symbol, limit=20):
        return {"bids": [[100, 1]], "asks": [[102, 1]]}

    async def fetch_trades(self, symbol, limit=50):
        return [{"price": 101}]

    async def close(self):
        return None


@pytest.mark.parametrize("provider_class", [OKXProvider, KrakenProvider, CoinbaseProvider])
async def test_new_crypto_provider_fixture_parsing(provider_class):
    provider = provider_class()
    provider.exchange = _FakeExchange()
    quote = await provider.get_quote("BTC/USDT")
    frame = await provider.get_ohlcv("BTC/USDT", "1m", 10)
    assert quote.last == 101 and quote.status == "LIVE"
    assert list(frame.columns) == ["Timestamp", "Open", "High", "Low", "Close", "Volume"]


def test_crypto_priority_and_optional_tradfi_activation(monkeypatch):
    pairs = [(name, name) for name in ("gate", "coinbase", "kraken", "okx", "bybit", "binance")]
    assert [name for name, _ in prioritize_providers(pairs)] == [
        "binance", "bybit", "okx", "kraken", "coinbase", "gate",
    ]
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    engine = DataEngine()
    assert "twelvedata" not in engine.layer.providers and "finnhub" not in engine.layer.providers
    assert engine.available_source_count("btc_usdt") >= 3

    monkeypatch.setenv("TWELVEDATA_API_KEY", "fixture-key")
    monkeypatch.setenv("FINNHUB_API_KEY", "fixture-key")
    keyed = DataEngine()
    assert "twelvedata" in keyed.layer.providers and "finnhub" in keyed.layer.providers


def test_universe_and_rendered_views_have_no_duplicate_exposure():
    universe = MarketUniverse().universe
    assert len(universe) == 127
    assert len({row["underlying"] for row in universe.values()}) == len(universe)
    assert len({row["display_symbol"] for row in universe.values()}) == len(universe)
    assert not {"gc_f", "es_f", "nq_f"}.intersection(universe)

    duplicate_scan = [
        {"symbol": "gold", "underlying": "XAU", "score": 70,
         "realtime_source": False, "data_age_ms": 900_000},
        {"symbol": "gc_f", "underlying": "XAU", "score": 60,
         "realtime_source": True, "data_age_ms": 100},
    ]
    assert [row["symbol"] for row in prepare_radar(duplicate_scan)] == ["gc_f"]

    overview = {
        "COMMODITIES": [{"market_id": "gold", "underlying": "XAU", "last": 10,
                          "realtime_source": False}],
        "FUTURES": [{"market_id": "gc_f", "underlying": "XAU", "last": 11,
                     "realtime_source": True}],
    }
    merged = enrich_overview(overview)
    assert sum(len(rows) for rows in merged.values()) == 1
    assert merged["FUTURES"][0]["market_id"] == "gc_f"


async def test_scanner_crypto_phase_precedes_tradfi_and_reports_progress():
    infos = {
        "eur_usd": {"asset_class": "FOREX"},
        "btc_usdt": {"asset_class": "CRYPTO"},
        "aapl": {"asset_class": "STOCKS"},
    }

    class Data:
        universe = SimpleNamespace(
            get_all_ids=lambda: list(infos), get_info=lambda symbol: infos[symbol]
        )

        def __init__(self):
            self.prepared = []

        async def prepare_scan_cycle(self, symbols):
            self.prepared.append(list(symbols))

    data = Data()
    scanner = ScannerEngine(data, None, None, None)

    async def fake_scan(symbol, _semaphore, strategy_mode=None):
        return {"symbol": symbol, "status": "LIVE"}

    scanner.scan_asset = fake_scan
    progress = []
    results = await scanner.scan_all(progress_callback=lambda row, done, total: progress.append(
        (row["symbol"], done, total)
    ))
    assert results[0]["symbol"] == "btc_usdt"
    assert data.prepared[0] == ["btc_usdt"]
    assert progress[-1][1:] == (3, 3)


def test_auto_start_and_arm_semantics_and_scanner_api_progress(monkeypatch):
    idx.apply_startup_automation({"auto_start_on_startup": "true",
                                  "auto_arm_on_startup": "false"})
    assert idx.bot_state["is_running"] is True and idx.bot_state["armed"] is False
    idx.apply_startup_automation({"auto_start_on_startup": "false",
                                  "auto_arm_on_startup": "true"})
    assert idx.bot_state["is_running"] is True and idx.bot_state["armed"] is True

    monkeypatch.setitem(idx.bot_state, "scanning", True)
    monkeypatch.setitem(idx.bot_state, "scan_progress_count", 4)
    monkeypatch.setitem(idx.bot_state, "scan_progress_total", 127)
    response = TestClient(idx.app).get("/api/scanner")
    assert response.json()["progress"] == "4/127"
    assert response.json()["scanning"] is True
    idx.bot_state["scanning"] = False


def test_dashboard_is_runtime_self_contained_and_i18n_keysets_match():
    response = TestClient(idx.app).get("/")
    assert response.status_code == 200
    html = response.text
    assert not re.search(r"(?:src|href)=[\"']https?://", html, re.I)
    assert "/css/app.css" in html and "/js/lucide.min.js" in html
    assert "API INJOIGNABLE" in html and "CALENDRIER HORS LIGNE" in html
    assert "LIVE" in html and "DIFFÉRÉ ~15 min" in html

    javascript = open("public/js/i18n.js", encoding="utf-8").read()
    packs = {}
    for language in ("en", "fr", "es", "de"):
        start = javascript.index(f"        {language}: {{")
        end = javascript.index("\n        }", start)
        packs[language] = set(re.findall(r"^\s{12}([A-Za-z][A-Za-z0-9]*):", javascript[start:end], re.M))
    assert packs["en"] == packs["fr"] == packs["es"] == packs["de"]
    assert len(packs["en"]) >= 150
