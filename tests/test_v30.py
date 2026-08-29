"""v3.0 contract: RR 2.0, news-window trade mode, offline memory, runtime persist, auth."""
from __future__ import annotations

import ast
import os
import time
from datetime import datetime as real_datetime

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.index as idx
from api.engines.constants import DEFAULT_RSI_RISK_REWARD, RSI_RISK_REWARD_BOUNDS
from api.engines.data_layer import DataLayer
from api.engines.data_providers.exchange_rest import (
    ccxt_to_kraken_pair,
    ccxt_to_okx_inst,
    parse_kraken_ohlc,
    parse_kraken_ticker,
    parse_okx_candles,
    parse_okx_ticker,
)
from api.engines.db_manager import DatabaseManager
from api.engines.market_universe import MarketUniverse
from api.engines.news_engine import NewsEngine, is_important_event
from api.engines.settings_schema import SETTINGS_SPEC, validate_settings
from api.engines.signal_engine import SignalEngine
from api.security import api_key_matches, issue_session_token, verify_session_token
from tests.test_offline_engine_coverage import FakeDateTime


def test_rsi_rr_default_is_two():
    assert DEFAULT_RSI_RISK_REWARD == 2.0
    assert RSI_RISK_REWARD_BOUNDS == (1.0, 2.0)
    assert SETTINGS_SPEC["risk_reward_ratio"]["default"] == "2.0"
    assert SETTINGS_SPEC["risk_reward_ratio"]["max"] == 2.0
    engine = SignalEngine()
    assert engine.effective_risk_reward("btc_usdt", "rsi") == 2.0


def test_news_window_mode_defaults_to_trade():
    assert SETTINGS_SPEC["news_window_mode"]["default"] == "trade"
    cleaned, errors = validate_settings({"news_window_mode": "nope"})
    assert cleaned["news_window_mode"] == "trade"
    assert errors
    assert idx.news_engine.news_window_mode == "trade"


def test_important_event_classifier():
    assert is_important_event({"impact": "High", "title": "US CPI m/m"})
    assert is_important_event({"impact": "High", "title": "Non-Farm Payrolls"})
    assert is_important_event({"impact": "High", "title": ""})
    assert not is_important_event({"impact": "Medium", "title": "CPI"})
    assert not is_important_event({"impact": "High", "title": "Building Permits"})


@pytest.mark.asyncio
async def test_news_trade_mode_does_not_block_important_window(monkeypatch):
    engine = NewsEngine()
    assert engine.news_window_mode == "trade"
    fixed = real_datetime(2026, 8, 20, 14, 30)
    monkeypatch.setattr("api.engines.news_engine.datetime", FakeDateTime(fixed))

    async def fake_fetch():
        return [{"impact": "High", "country": "USD", "title": "CPI m/m",
                 "date": "Thu Aug 20", "time": "10:15am"}]

    monkeypatch.setattr(engine.provider, "fetch_events", fake_fetch)
    res = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert res["news_ok"] is True
    assert res["trading_allowed"] is True
    assert res["in_news_window"] is True

    engine.set_window_mode("avoid")
    blocked = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert blocked["news_ok"] is False
    assert blocked["trading_allowed"] is False


def test_last_quote_tables_and_roundtrip(tmp_path):
    db = DatabaseManager(str(tmp_path / "persist.db"))
    with db._get_connection() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "last_quotes" in names
    assert "last_ohlcv" in names
    db.save_last_quote("btc_usdt", {
        "symbol": "BTC/USDT", "asset_class": "CRYPTO", "exchange": "Kraken",
        "timestamp": 1, "last": 100.0, "source": "Kraken", "status": "LIVE",
    })
    loaded = db.load_last_quote("btc_usdt")
    assert loaded["last"] == 100.0
    db.save_last_ohlcv("btc_usdt", "1m", [{"Timestamp": 1, "Open": 1, "High": 2,
                                           "Low": 0.5, "Close": 1.1, "Volume": 9}])
    rows = db.load_last_ohlcv("btc_usdt", "1m")
    assert rows[0]["Close"] == 1.1
    assert db.load_last_quote("btc_usdt", max_age_s=0) is None


@pytest.mark.asyncio
async def test_data_layer_restores_stale_quote(tmp_path):
    db = DatabaseManager(str(tmp_path / "layer.db"))
    layer = DataLayer()
    layer.attach_persistence(db)
    catalog = MarketUniverse()

    class Dead:
        async def get_quote(self, *a, **k):
            return None

        async def get_ohlcv(self, *a, **k):
            return pd.DataFrame()

    db.save_last_quote("btc_usdt", {
        "symbol": "BTC/USDT", "name": "BTC/USDT", "asset_class": "CRYPTO",
        "exchange": "Kraken", "timestamp": int(time.time() * 1000),
        "last": 42.0, "source": "Kraken", "status": "LIVE",
    })
    layer.register_provider("gate", Dead())
    quotes = await layer.get_all_quotes(["btc_usdt"], catalog)
    assert quotes
    assert quotes[0].status == "STALE"
    assert quotes[0].last == 42.0
    assert "(cached)" in quotes[0].source


def test_runtime_intent_restore_keeps_running():
    original = dict(idx.bot_state)
    try:
        idx.bot_state.update(is_running=False, armed=False, mode="DEMO")
        idx.apply_startup_automation({
            "persist_runtime_state": "true",
            "runtime_intent_saved": "true",
            "runtime_is_running": "true",
            "runtime_armed": "true",
            "runtime_mode": "DEMO",
            "auto_arm_on_startup": "false",
            "auto_start_on_startup": "false",
        })
        assert idx.bot_state["is_running"] is True
        assert idx.bot_state["armed"] is True
        idx.bot_state.update(is_running=False, armed=False, mode="DEMO")
        idx.apply_startup_automation({
            "persist_runtime_state": "true",
            "runtime_intent_saved": "false",
            "auto_arm_on_startup": "false",
            "auto_start_on_startup": "false",
        })
        assert idx.bot_state["is_running"] is False
        assert idx.bot_state["armed"] is False
    finally:
        idx.bot_state.clear()
        idx.bot_state.update(original)


def test_session_token_roundtrip_and_timing_safe():
    token = issue_session_token("super-secret")
    assert verify_session_token(token, "super-secret") is True
    assert verify_session_token(token, "other") is False
    assert api_key_matches("super-secret", "super-secret") is True
    assert api_key_matches("nope", "super-secret") is False
    assert api_key_matches("", "super-secret") is False


@pytest.mark.asyncio
async def test_require_admin_accepts_none_request():
    original = idx.ADMIN_API_KEY
    try:
        idx.ADMIN_API_KEY = "k"
        with pytest.raises(HTTPException) as exc:
            await idx.require_admin(x_api_key="wrong")
        assert exc.value.status_code == 401
        await idx.require_admin(x_api_key="k")
    finally:
        idx.ADMIN_API_KEY = original


def test_login_logout_cookie_flow():
    original = idx.ADMIN_API_KEY
    idx.auth_guard.reset()
    try:
        idx.ADMIN_API_KEY = "prod-key"
        client = TestClient(idx.app)
        bad = client.post("/api/login", json={"api_key": "nope"})
        assert bad.status_code == 401
        ok = client.post("/api/login", json={"api_key": "prod-key"})
        assert ok.status_code == 200
        assert ok.json()["success"] is True
        assert "qtp_session" in ok.cookies
        out = client.post("/api/logout")
        assert out.status_code == 200
    finally:
        idx.ADMIN_API_KEY = original
        idx.auth_guard.reset()


def test_app_version_and_docs_disabled_outside_testing():
    assert idx.app.version == "3.1.0"
    assert getattr(idx.news_engine, "news_window_mode", "trade") == "trade"


def test_kraken_okx_rest_parsers():
    assert ccxt_to_kraken_pair("BTC/USDT") == "XBTUSDT"
    assert ccxt_to_okx_inst("BTC/USDT") == "BTC-USDT"
    kraken = parse_kraken_ticker({
        "error": [],
        "result": {"XBTUSDT": {
            "c": ["65000.1", "1"], "b": ["64990", "1"], "a": ["65010", "1"],
            "v": ["1", "12.5"], "o": "64000",
        }},
    }, "BTC/USDT")
    assert kraken is not None and kraken.last == pytest.approx(65000.1)
    assert kraken.status == "LIVE"
    okx = parse_okx_ticker({
        "code": "0",
        "data": [{"last": "100.5", "bidPx": "100.4", "askPx": "100.6",
                  "ts": "1777000000000", "vol24h": "9"}],
    }, "BTC/USDT")
    assert okx is not None and okx.last == pytest.approx(100.5)
    k_ohlc = parse_kraken_ohlc({
        "result": {"XBTUSDT": [[1700000000, "1", "2", "0.5", "1.5", "10", "11", 4]]},
    })
    assert not k_ohlc.empty
    o_ohlc = parse_okx_candles({
        "data": [["1700000001000", "2", "3", "1", "2.5", "8"]],
    })
    assert not o_ohlc.empty


def test_official_frontend_v30_contract():
    html = open("public/index.html", encoding="utf-8").read()
    assert "RSI target RR" in html
    assert "<strong>2.0</strong>" in html
    assert 'id="set-risk_reward_ratio" min="1.0" max="2.0"' in html
    assert "credentials: 'include'" in html
    assert "sessionStorage.setItem('qtp-api-key'" in html
    assert "sessionStorage.getItem('qtp-api-key'" in html
    assert "set-news_window_mode" in html
    ast.parse(open("api/index.py", encoding="utf-8").read())
    assert os.path.exists("docs/AUDIT_V30.md")
    assert os.path.exists("docs/AGENT_PROMPT_APPLY_V30.md")
