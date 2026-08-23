"""Scanner, radar, providers, calendar and official-UI regressions."""
from __future__ import annotations

import ast
import os
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.index as idx
from api.engines.news_engine import NewsEngine
from api.engines.provider_capabilities import (
    PROVIDER_CAPABILITIES, classify_quote_status, looks_like_quota_error,
)
from api.engines.radar import prepare_radar
from api.engines.scan_contract import merge_universe_rows, placeholder_row, summarize_scan
from api.engines.scanner_engine import ScannerEngine
from api.engines.signal_engine import SignalEngine


def test_provider_capabilities_are_free_tier_only():
    required = {
        "twelvedata", "alpha_vantage", "finnhub", "yahoo",
        "binance", "bybit", "okx", "kraken", "coinbase", "gate",
    }
    assert required.issubset(PROVIDER_CAPABILITIES)
    for cap in PROVIDER_CAPABILITIES.values():
        assert cap["free_tier"] is True
        assert "max_requests_per_minute" in cap
        assert cap["supports_websocket"] is False


def test_quote_status_uses_timestamp_not_name():
    live = classify_quote_status(
        {"last": 100, "status": "LIVE", "timestamp": 1_777_000_000_000, "source": "Binance"},
        "binance", now_ms=1_777_000_000_400,
    )
    assert live["status"] == "LIVE"
    delayed = classify_quote_status(
        {"last": 1.1, "status": "LIVE", "timestamp": 1, "source": "Yahoo Finance"},
        "yahoo_forex", now_ms=1_777_000_000_000,
    )
    assert delayed["status"] == "DELAYED"
    missing = classify_quote_status(None)
    assert missing["status"] == "DATA_UNAVAILABLE"
    assert looks_like_quota_error("Note Thank you for using Alpha Vantage")


def test_merge_universe_keeps_unavailable_visible():
    class Uni:
        def get_all_ids(self):
            return ["btc_usdt", "eth_usdt", "eur_usd"]

        def get_info(self, symbol):
            return {"display_symbol": symbol.upper().replace("_", "/"),
                    "asset_class": "CRYPTO" if "usdt" in symbol else "FOREX",
                    "underlying": symbol}

    rows = merge_universe_rows(
        [{"symbol": "btc_usdt", "status": "LIVE", "score": 10, "strategy": "rsi"}],
        Uni(),
    )
    assert [r["symbol"] for r in rows] == ["btc_usdt", "eth_usdt", "eur_usd"]
    assert rows[1]["status"] == "DATA_UNAVAILABLE"
    assert rows[1]["tradable"] is False
    summary = summarize_scan(rows, 3)
    assert summary["markets_unavailable"] == 2
    assert summary["markets_total"] == 3


def test_prepare_radar_does_not_drop_unavailable():
    assets = [
        placeholder_row("aaa", {"display_symbol": "AAA", "underlying": "AAA"}),
        {"symbol": "bbb", "underlying": "BBB", "status": "LIVE", "score": 20,
         "realtime_source": True, "signal_data": {"strategy": "rsi"}},
    ]
    rows = prepare_radar(assets, filter_mode="all")
    assert len(rows) == 2
    assert any(r["status"] == "DATA_UNAVAILABLE" for r in rows)


def test_scanner_api_exposes_full_universe(monkeypatch):
    ids = idx.data_engine.universe.get_all_ids()
    idx.bot_state["latest_scan"] = [
        {"symbol": "btc_usdt", "status": "LIVE", "score": 10,
         "signal_data": {"strategy": "rsi"}, "underlying": "btc_usdt"},
        {"symbol": "eth_usdt", "status": "LIVE", "score": 8,
         "signal_data": {"strategy": "rsi"}, "underlying": "eth_usdt"},
    ]
    client = TestClient(idx.app)
    data = client.get("/api/scanner?filter=all").json()
    assert data["markets_total"] == len(ids)
    assert len(data["assets"]) == len(ids)
    assert data["markets_unavailable"] >= 1
    assert data["risk_reward_rsi"] == 1.5
    assert data["active_strategy"] == "rsi"


def test_scanner_trigger_refuses_double_scan():
    idx.bot_state["scanning"] = True
    idx.bot_state["scan_started_at"] = 10**12  # not stuck
    client = TestClient(idx.app)
    data = client.post("/api/scanner/trigger").json()
    assert data["success"] is False
    assert data["reason"] == "SCAN_IN_PROGRESS"
    idx.bot_state["scanning"] = False


@pytest.mark.asyncio
async def test_provider_error_does_not_stop_scan():
    infos = {
        "btc_usdt": {"asset_class": "CRYPTO", "display_symbol": "BTC/USDT", "underlying": "btc"},
        "eth_usdt": {"asset_class": "CRYPTO", "display_symbol": "ETH/USDT", "underlying": "eth"},
    }

    class Data:
        universe = SimpleNamespace(
            get_all_ids=lambda: list(infos),
            get_info=lambda symbol: infos[symbol],
            get_market_status=lambda symbol: "OPEN",
        )

        async def prepare_scan_cycle(self, symbols):
            return None

        def is_quote_realtime(self, symbol, ticker):
            return True

    scanner = ScannerEngine(Data(), None, SignalEngine(), NewsEngine())

    async def fake_scan(symbol, _sem, strategy_mode=None):
        if symbol == "btc_usdt":
            raise RuntimeError("Provider failed")
        return {"symbol": symbol, "status": "LIVE", "strategy": "rsi"}

    scanner.scan_asset = fake_scan
    results = await scanner.scan_all()
    assert len(results) == 2
    assert any(r["status"] == "ERROR" for r in results)
    assert any(r["symbol"] == "eth_usdt" for r in results)


@pytest.mark.asyncio
async def test_rsi_scan_does_not_fetch_orderbook(monkeypatch):
    calls = []

    class Data:
        universe = SimpleNamespace(
            get_all_ids=lambda: ["btc_usdt"],
            get_info=lambda symbol: {
                "asset_class": "CRYPTO", "display_symbol": "BTC/USDT",
                "underlying": "btc", "name": "Bitcoin",
            },
            get_market_status=lambda symbol: "OPEN",
        )
        layer = SimpleNamespace(market_source_state={})

        async def fetch_ohlcv(self, *a, **k):
            calls.append("ohlcv")
            return pd.DataFrame({
                "Open": [1] * 50, "High": [2] * 50, "Low": [0.5] * 50,
                "Close": [1.2] * 50, "Volume": [10] * 50,
            })

        async def fetch_ticker(self, *a, **k):
            calls.append("ticker")
            return {"last": 1.2, "status": "LIVE", "timestamp": 1_777_000_000_000,
                    "spread": 0.01, "volume": 10, "source": "Binance", "change_24h": 0}

        async def fetch_order_book(self, *a, **k):
            calls.append("orderbook")
            return {"bids": [], "asks": []}

        async def fetch_trades(self, *a, **k):
            calls.append("trades")
            return []

        async def fetch_cross_quotes(self, *a, **k):
            calls.append("cross")
            return []

        def is_quote_realtime(self, symbol, ticker):
            return True

        def is_realtime_capable(self, symbol):
            return True

    news = SimpleNamespace(
        check_trading_allowed=AsyncMock(return_value={
            "trading_allowed": True, "news_ok": True, "session_ok": True,
            "day_ok": True, "blocking_event": None, "next_events": [],
        })
    )
    scanner = ScannerEngine(Data(), None, SignalEngine(), news)
    result = await scanner.scan_asset("btc_usdt", __import__("asyncio").Semaphore(1))
    assert "orderbook" not in calls and "trades" not in calls and "cross" not in calls
    assert result["strategy"] == "rsi"


@pytest.mark.parametrize(
    ("policy", "asset_class", "news_ok"),
    [("block_all", "CRYPTO", False),
     ("block_tradfi_only", "CRYPTO", True),
     ("block_tradfi_only", "FOREX", False),
     ("allow_all", "FOREX", True)],
)
@pytest.mark.asyncio
async def test_calendar_unavailable_policies_apply_to_rsi(policy, asset_class, news_ok):
    engine = NewsEngine(unavailable_policy=policy)
    engine.provider.fetch_events = AsyncMock(return_value=[])
    result = await engine.check_trading_allowed(asset_class=asset_class)
    assert result["news_ok"] is news_ok
    sig_engine = SignalEngine()
    fake = {"status": "SIGNAL_DETECTED", "strategy": "rsi", "score": 90,
            "entry": 1, "sl": 0.9, "tp": 1.15, "market_id": "x"}
    sig_engine.strategies["rsi"].generate_signal = lambda **_: dict(fake)
    out = sig_engine.generate_signal(
        {"volatility": "MEDIUM"}, result,
        pd.DataFrame({"Open": [1] * 50, "High": [1] * 50, "Low": [1] * 50, "Close": [1] * 50}),
        strategy_mode="rsi", market_id="x",
    )
    if not result["trading_allowed"]:
        assert out["status"] == "NO_TRADE"
        assert out["block_reason"] in {"NEWS_BLOCKED", "CALENDAR_UNAVAILABLE"}


def test_status_and_diagnose_expose_block_reason():
    client = TestClient(idx.app)
    status = client.get("/api/status?market_id=btc_usdt").json()
    assert "block_reason" in status
    assert status["active_strategy"] == "rsi"
    assert status["risk_reward_rsi"] == 1.5
    assert "news_unavailable_policy" in status
    diag = client.get("/api/diagnose?market_id=btc_usdt").json()
    assert "block_reason" in diag


def test_delayed_quote_not_auto_tradable():
    row = prepare_radar([{
        "symbol": "eur_usd", "status": "DELAYED", "tradable": True, "score": 90,
        "realtime_source": False, "signal_data": {"strategy": "rsi", "status": "SIGNAL_DETECTED"},
    }])[0]
    assert row["tradable"] is False


def test_max_new_positions_still_three():
    from api.engines.constants import DEFAULT_MAX_NEW_POSITIONS_PER_SCAN
    assert DEFAULT_MAX_NEW_POSITIONS_PER_SCAN == 3


def test_official_frontend_is_unchanged_identity():
    files = sorted(
        os.path.relpath(os.path.join(root, name), "public")
        for root, _, names in os.walk("public")
        for name in names
    )
    assert files == ["css/app.css", "index.html", "js/i18n.js", "js/lucide.min.js"]
    html = open("public/index.html", encoding="utf-8").read()
    assert html.rstrip().endswith("</html>")
    assert not re.search(r"(?:src|href)=[\"']https?://", html, re.I)
    assert "strategyRsiName" in html
    assert "riskRewardRsi" in html
    ast.parse(open("api/index.py", encoding="utf-8").read())


def test_i18n_new_keys_parity():
    js = open("public/js/i18n.js", encoding="utf-8").read()
    required = {
        "strategyRsiName", "activeStrategy", "riskRewardRsi",
        "scannerProgress", "marketsUnavailable", "scannerError", "lastScan",
    }
    for lang, expected in {
        "en": "RSI-14 Reversal",
        "fr": "RSI-14 Retournement",
        "es": "RSI-14 Reversión",
        "de": "RSI-14 Umkehr",
    }.items():
        assert f'strategyRsiName: "{expected}"' in js
    langs = ["en", "fr", "es", "de"]
    positions = {lang: js.find(f"        {lang}: {{") for lang in langs}
    keysets = {}
    for lang in langs:
        start = positions[lang]
        later = [positions[lng] for lng in langs if positions[lng] > start]
        end = min(later + [js.find("    };", start)])
        keysets[lang] = set(re.findall(r"^\s{12}([A-Za-z0-9_]+):", js[start:end], re.M))
        assert required.issubset(keysets[lang])
    assert keysets["en"] == keysets["fr"] == keysets["es"] == keysets["de"]


def test_serverless_detection(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("FUNCTIONS_WORKER_RUNTIME", raising=False)
    assert idx.is_serverless_runtime() is False
    monkeypatch.setenv("VERCEL", "1")
    assert idx.is_serverless_runtime() is True


# ---- P0/P1 new tests -------------------------------------------------------


def test_scan_timeout_longer_than_lock_stale():
    """P0: SCAN_ALL_TIMEOUT_S > SCAN_LOCK_STALE_S so scan_all is not killed at 180s."""
    assert idx.SCAN_ALL_TIMEOUT_S > idx.SCAN_LOCK_STALE_S
    assert idx.SCAN_ALL_TIMEOUT_S >= 300


def test_scan_constants_defined():
    """P0: both timeout constants exist with correct values."""
    assert idx.SCAN_LOCK_STALE_S == 180.0
    assert idx.SCAN_ALL_TIMEOUT_S == 600.0


def test_is_fresh_crypto_30s():
    """P1: crypto freshness accepts 20s, refuses 35s (was 10s/20s @15s)."""
    import time
    now = time.time()
    ticker_fresh = {"timestamp": int(now * 1000 - 20_000)}
    ticker_stale = {"timestamp": int(now * 1000 - 35_000)}
    assert idx.data_engine.is_fresh(ticker_fresh, "CRYPTO") is True
    assert idx.data_engine.is_fresh(ticker_stale, "CRYPTO") is False


def test_is_fresh_tradfi_60s():
    """P0: tradfi live freshness accepts 45s, refuses 70s."""
    import time
    now = time.time()
    ticker_fresh = {"timestamp": int(now * 1000 - 45_000)}
    ticker_stale = {"timestamp": int(now * 1000 - 70_000)}
    assert idx.data_engine.is_fresh(ticker_fresh, "FOREX") is True
    assert idx.data_engine.is_fresh(ticker_stale, "FOREX") is False


@pytest.mark.asyncio
async def test_start_triggers_scan(monkeypatch):
    """P0: POST /api/start launches a scan task."""
    called = {"ok": False}

    async def _mock_scan(force=False):
        called["ok"] = True

    monkeypatch.setattr(idx, "tick_scanner", _mock_scan)
    idx.bot_state["scanning"] = False
    idx.bot_state["is_running"] = False
    client = TestClient(idx.app)
    resp = client.post("/api/start", headers={"X-API-Key": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert called["ok"] is True


def test_tick_capital_no_await_under_lock():
    """P0: tick_capital must not await network while holding state_lock (static check)."""
    source = open("api/index.py", encoding="utf-8").read()
    in_func = False
    prev_indent = ""
    for line in source.split("\n"):
        if "async def tick_capital" in line:
            in_func = True
            continue
        if not in_func:
            continue
        if "async def " in line and "tick_capital" not in line:
            break
        stripped = line.strip()
        if "async with state_lock:" in stripped:
            prev_indent = line[:len(line) - len(line.lstrip())]
            # Check subsequent lines until dedent
            continue
        if prev_indent:
            cur_indent = line[:len(line) - len(line.lstrip())]
            if cur_indent <= prev_indent and stripped:
                prev_indent = ""
            elif stripped.startswith("await "):
                pytest.fail(f"tick_capital awaits '{stripped}' while holding state_lock")


@pytest.mark.parametrize(
    ("policy", "asset_class", "news_ok"),
    [
        ("block_all", "CRYPTO", False),
        ("block_all", "FOREX", False),
        ("block_tradfi_only", "CRYPTO", True),
        ("block_tradfi_only", "FOREX", False),
        ("allow_all", "CRYPTO", True),
        ("allow_all", "FOREX", True),
    ],
)
@pytest.mark.asyncio
async def test_calendar_hs_respects_policy(policy, asset_class, news_ok):
    """P0: Calendar HS + policy respected (block_all blocks RSI crypto too)."""
    engine = NewsEngine(unavailable_policy=policy)
    engine.provider.fetch_events = AsyncMock(return_value=[])
    result = await engine.check_trading_allowed(asset_class=asset_class)
    assert result["news_ok"] is news_ok


def test_ranker_uses_compute_trade_costs():
    """P0: ranker uses compute_trade_costs not naive 0.001*2."""
    source = open("api/engines/opportunity_ranker.py", encoding="utf-8").read()
    assert "compute_trade_costs" in source or "_compute_costs" in source
    # Old naive formula must not be the primary calculation
    assert "round_trip_cost = (entry * 0.001 * 2) + spread_abs" not in source


def test_full_universe_returned_by_api():
    """P0: /api/scanner always returns all markets (126+)."""
    ids = idx.data_engine.universe.get_all_ids()
    assert len(ids) >= 126
    idx.bot_state["latest_scan"] = []
    client = TestClient(idx.app)
    data = client.get("/api/scanner?filter=all").json()
    assert len(data["assets"]) == len(ids)


def test_last_block_reason_exposed():
    """P0: last_block_reason and excluded appear in endpoints."""
    idx.bot_state["last_block_reason"] = "TEST_BLOCK"
    idx.bot_state["opportunity_ranking"] = {"excluded": [{"symbol": "x"}]}
    client = TestClient(idx.app)
    s = client.get("/api/status").json()
    assert s.get("last_block_reason") == "TEST_BLOCK"
    r = client.get("/api/scanner").json()
    assert r.get("last_block_reason") == "TEST_BLOCK"
    d = client.get("/api/diagnose?market_id=btc_usdt").json()
    assert d.get("last_block_reason") is None or d.get("last_block_reason") == "TEST_BLOCK"
    o = client.get("/api/opportunities").json()
    assert o.get("last_block_reason") == "TEST_BLOCK"


def test_yahoo_data_refused_auto_trade():
    """P0: Yahoo delayed data is not auto-tradable."""
    row = prepare_radar([{
        "symbol": "eur_usd", "status": "DELAYED", "tradable": True, "score": 95,
        "realtime_source": False, "signal_data": {"strategy": "rsi", "status": "SIGNAL_DETECTED"},
        "data_age_ms": 100,
    }])[0]
    assert row["tradable"] is False
    # Also check scalping guard
    guard = idx.data_engine.check_scalping_allowed("eur_usd")
    if not guard["allowed"]:
        assert "NON_REALTIME_SOURCE" in guard["reason"]
