"""Offline coverage for remaining red lines in signal/execution/news/data_layer."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from api.engines.data_layer import DataLayer
from api.engines.data_providers.base_provider import TickerModel
from api.engines.execution_engine import ExecutionEngine
from api.engines.market_universe import MarketUniverse
from api.engines.news_engine import EconomicCalendarProvider, NewsEngine, SessionFilter
from api.engines.signal_engine import SignalEngine
from api.engines.db_manager import DatabaseManager
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.risk_engine import RiskEngine
from tests.mocks import build_ohlcv, build_ticker


def test_signal_setters_and_effective_rr_fallbacks():
    eng = SignalEngine()
    eng.set_min_score("not-int")
    assert eng.min_score >= 84
    eng.set_risk_reward("bad")
    eng.set_atr_stop_multiplier("bad")
    eng.set_market_tuning({"btc_usdt": {"risk_reward": "not-a-float"}})
    assert eng.effective_risk_reward("btc_usdt", "rsi") == 1.5
    eng.set_market_tuning({"btc_usdt": {"risk_reward": -1}})
    assert 1.0 <= eng.effective_risk_reward("btc_usdt", "rsi") <= 2.0
    eng.set_regime_adaptation(False)
    assert eng._regime_of({"volatility": "HIGH"}) == "NORMAL"


def test_signal_multi_mode_picks_best():
    eng = SignalEngine()
    rsi = MagicMock()
    rsi.generate_signal = MagicMock(return_value={
        "status": "SIGNAL_DETECTED", "score": 92, "reason": "rsi",
        "entry": 100, "sl": 95, "tp": 110,
    })
    rsi.set_risk_reward = MagicMock()
    tape = MagicMock()
    tape.generate_signal = MagicMock(return_value={
        "status": "SIGNAL_DETECTED", "score": 88, "reason": "tape",
        "entry": 1, "sl": 0.9, "tp": 1.2,
    })
    eng.strategies["rsi"] = rsi
    eng.strategies["tape"] = tape
    eng.active_strategy_names = ["rsi", "tape"]
    out = eng.generate_signal(
        {"volatility": "MEDIUM"}, {"trading_allowed": True},
        build_ohlcv(), strategy_mode="multi", market_id="btc_usdt",
    )
    assert out["status"] == "SIGNAL_DETECTED"
    assert out.get("multi_strategy") is True
    empty = SignalEngine()
    empty.active_strategy_names = ["rsi"]
    empty.strategies["rsi"].generate_signal = MagicMock(
        return_value={"status": "NO_TRADE", "score": 0, "reason": "none"})
    none = empty.generate_signal(
        {"volatility": "MEDIUM"}, {"trading_allowed": True},
        build_ohlcv(), strategy_mode="multi", market_id="btc_usdt",
    )
    assert none["status"] == "NO_TRADE"


def test_signal_quality_cost_and_volatile_threshold():
    eng = SignalEngine(min_score=84, fee_pct=10.0, slippage_pct=10.0, max_cost_ratio=0.01)
    res = {
        "status": "SIGNAL_DETECTED", "score": 90, "entry": 100, "sl": 99.99,
        "market_id": "btc_usdt",
    }
    out = eng._apply_quality_gates(res, "rsi", "NORMAL")
    assert out["status"] == "NO_TRADE" or out.get("cost_blocked")
    low = {"status": "SIGNAL_DETECTED", "score": 70, "market_id": "x"}
    out2 = eng._apply_quality_gates(low, "rsi", "VOLATILE")
    assert out2["status"] == "NO_TRADE"


@pytest.mark.asyncio
async def test_execution_time_stop_and_market_closed(tmp_path):
    db = DatabaseManager(str(tmp_path / "ex.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_latency_ms', '0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_rejection_prob', '0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('max_trade_duration_minutes', '1')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trailing_stop_active', 'true')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('partial_tp_ratio', '1.0')")
    portfolio = PortfolioEngine(db)
    risk = RiskEngine()
    universe = SimpleNamespace(get_market_status=lambda s: "CLOSED")
    engine = ExecutionEngine(portfolio, db, risk, universe)
    old = datetime.now() - timedelta(minutes=5)
    pos = {
        "id": "T1", "mode": "DEMO", "symbol": "btc_usdt", "display_symbol": "BTC/USDT",
        "direction": "BUY", "entry_price": 100.0, "quantity": 1.0, "sl": 95.0, "tp": 110.0,
        "leverage": 1.0, "fees": 0.0, "status": "OPEN", "open_time": old.isoformat(),
        "initial_quantity": 1.0, "remaining_quantity": 1.0, "entry_fees": 0.0,
        "slippage_cost": 0.0, "funding_cost": 0.0, "partial_realized_pnl": 0.0,
        "metadata": {"atr": 1.0},
    }
    db.save_trade(pos)
    closed = await engine.update_active_positions("DEMO", {"btc_usdt": build_ticker(last=101)})
    assert closed and closed[0]["metadata"]["close_reason"] == "MARKET_CLOSED_PROTECTION"


@pytest.mark.asyncio
async def test_execution_pending_limit_fill(tmp_path):
    db = DatabaseManager(str(tmp_path / "pend.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_latency_ms', '0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_rejection_prob', '0')")
    engine = ExecutionEngine(
        PortfolioEngine(db), db, RiskEngine(),
        SimpleNamespace(get_market_status=lambda s: "OPEN"),
    )
    engine.pending_orders = [{
        "id": "P1", "mode": "DEMO", "market_id": "btc_usdt",
        "signal": {"market_id": "btc_usdt", "direction": "BUY", "sl": 90, "tp": 110,
                   "display_symbol": "BTC/USDT"},
        "risk": {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1},
        "order_type": "LIMIT", "direction": "BUY", "limit_price": 101,
        "status": "PENDING",
    }, {
        "id": "P2", "mode": "REAL", "market_id": "eth_usdt",
        "signal": {}, "risk": {}, "order_type": "LIMIT", "direction": "BUY",
        "limit_price": 1,
    }]
    filled = await engine.process_pending_orders("DEMO", {
        "btc_usdt": {"last": 100, "ask": 100, "bid": 99.9},
    })
    assert filled
    assert any(p.get("mode") == "REAL" for p in engine.pending_orders)


@pytest.mark.asyncio
async def test_news_calendar_fallbacks_mocked(monkeypatch):
    class BoomClient:
        def __init__(self, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("no client")

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("api.engines.news_engine.httpx.AsyncClient", BoomClient)
    db = MagicMock()
    db.load_calendar_cache = MagicMock(side_effect=RuntimeError("cache read"))
    db.save_calendar_cache = MagicMock(side_effect=RuntimeError("cache write"))
    provider = EconomicCalendarProvider(db)
    events = await provider.fetch_events()
    assert events == []
    assert provider.status == "DATA_UNAVAILABLE"

    # JSON 200 invalid + HTML 200 empty + persisted hit
    class Resp:
        def __init__(self, code, payload=None, text=""):
            self.status_code = code
            self._payload = payload
            self.text = text

        def json(self):
            raise ValueError("bad json")

    class Dual:
        def __init__(self, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, **k):
            if "json" in url:
                return Resp(200, text='{"events":[]}')
            return Resp(200, text="<html></html>")

    monkeypatch.setattr("api.engines.news_engine.httpx.AsyncClient", Dual)
    db2 = MagicMock()
    db2.load_calendar_cache = MagicMock(return_value={
        "events": [{"title": "CPI", "impact": "High"}],
        "fetched_at": time.time(),
        "source": "old",
    })
    p2 = EconomicCalendarProvider(db2)
    cached = await p2.fetch_events()
    assert cached[0]["title"] == "CPI"
    assert p2.status == "CACHED"

    # parse_json dict wrapper + bad impact + iso without tz
    ev = p2._parse_json({
        "events": [
            {"title": "X", "date": "2026-08-20T08:30:00", "impact": "Mega"},
            {"nope": 1},
        ]
    })
    assert ev[0]["impact"] == "Low"
    assert p2._parse_json("nope") == []

    # memory last_known path
    p3 = EconomicCalendarProvider()
    p3.cache = [{"title": "mem"}]
    p3.source_fetched_at = time.time()
    p3.last_update = None
    monkeypatch.setattr("api.engines.news_engine.httpx.AsyncClient", BoomClient)
    mem = await p3.fetch_events()
    assert mem[0]["title"] == "mem"


def test_session_filter_forex_friday_close(monkeypatch):
    class FakeDT:
        @staticmethod
        def now(tz=None):
            # Friday 22:30
            import pytz
            dt = datetime(2026, 8, 21, 22, 30)
            return pytz.timezone("Europe/Paris").localize(dt)

        strftime = datetime.strftime

    monkeypatch.setattr("api.engines.news_engine.datetime", FakeDT)
    s = SessionFilter()
    fx = s.is_trading_allowed("FOREX")
    assert fx["session_ok"] is False


def test_news_engine_policy_and_apply():
    eng = NewsEngine(unavailable_policy="nope")
    # P0-1: the fail-open-crypto default is block_tradfi_only (crypto trades
    # when the calendar is down; tradfi stays blocked).
    assert eng.news_unavailable_policy == "block_tradfi_only"
    eng.apply_settings({})
    assert eng.news_unavailable_policy == "block_tradfi_only"
    eng.apply_settings({"news_unavailable_policy": "allow_all"})
    assert eng._outage_allows("FOREX") is True
    eng.apply_settings({"news_unavailable_policy": "block_tradfi_only"})
    assert eng._outage_allows("CRYPTO") is True
    assert eng._outage_allows("FOREX") is False


@pytest.mark.asyncio
async def test_data_layer_prune_and_fallback_paths():
    layer = DataLayer()
    # prune huge cache
    layer.failure_cache = {f"k{i}": time.time() - 4000 for i in range(2001)}
    layer.failure_counts = {f"k{i}": 1 for i in range(2001)}
    layer._prune_failure_cache()
    assert len(layer.failure_cache) < 2001

    class EmptyProv:
        async def get_ohlcv(self, *a, **k):
            return pd.DataFrame()

        async def get_order_book(self, *a):
            return None

        async def get_recent_trades(self, *a):
            return None

        async def get_quote(self, *a):
            raise RuntimeError("possibly delisted")

        async def health_check(self):
            return "not-a-dict"

    class OkProv:
        async def get_ohlcv(self, *a, **k):
            return pd.DataFrame({"Close": [1, 2]})

        async def get_order_book(self, *a):
            return {"bids": [[1, 1]], "asks": [[2, 1]]}

        async def get_recent_trades(self, *a):
            return [{"price": 1}]

        async def get_quote(self, *a):
            return TickerModel(
                symbol="BTC/USDT", asset_class="CRYPTO", exchange="t",
                timestamp=int(time.time() * 1000), last=100.0, source="t", status="LIVE",
            )

        async def health_check(self):
            raise RuntimeError("down")

    catalog = MarketUniverse()
    layer.register_provider("gate", EmptyProv())
    assert (await layer.get_ohlcv("btc_usdt", "1m", catalog=catalog)).empty
    assert await layer.get_order_book("btc_usdt", catalog) is None
    assert await layer.get_trades("btc_usdt", catalog) is None
    assert await layer.get_ohlcv("nope", "1m", catalog=catalog) is not None
    assert (await layer.get_ohlcv("btc_usdt", "1m")).empty

    layer2 = DataLayer()
    layer2.register_provider("gate", OkProv())
    df = await layer2.get_ohlcv("btc_usdt", "1m", catalog=catalog)
    assert not df.empty
    assert (await layer2.get_order_book("btc_usdt", catalog))["bids"]
    assert await layer2.get_trades("btc_usdt", catalog)
    quotes = await layer2.get_all_quotes(["btc_usdt"], catalog)
    assert quotes
    health = await layer2.get_health()
    assert health[0]["status"] == "OFFLINE"

    # cooldown skip
    layer2.failure_cache["ohlcv:gate:BTC/USDT"] = time.time()
    layer2.failure_counts["ohlcv:gate:BTC/USDT"] = 1
    empty = await layer2.get_ohlcv("btc_usdt", "1m", catalog=catalog)
    assert empty.empty

    # subscriber broadcast fail
    class Sub:
        async def broadcast(self, msg):
            raise RuntimeError("ws")

    layer2.subscribers = [Sub(), object()]
    await layer2.broadcast_update({"x": 1})
