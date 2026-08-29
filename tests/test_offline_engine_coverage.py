"""
LOT G — Offline coverage of the remaining engines (no network, full mocks).

Sections: NewsEngine (calendar parsing / risk / sessions), BacktestEngine,
BrokerConnector (fake adapters), NotificationEngine (fake httpx),
YahooProvider (fake yfinance), RiskEngine lifecycle extras, MarketUniverse
session logic with a fixed clock.
"""
from datetime import datetime as real_datetime

import pandas as pd
import pytest

from api.engines.analysis_engine import AnalysisEngine
from api.engines.backtest_engine import BacktestEngine
from api.engines.broker_connector import BrokerConnector
from api.engines.data_providers.yahoo_provider import YahooProvider
from api.engines.market_universe import MarketUniverse
from api.engines.news_engine import (EventRiskEngine, EconomicCalendarProvider,
                                     NewsEngine, NewsFilter, SessionFilter)
from api.engines.notification_engine import NotificationEngine
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine
from api.engines.db_manager import DatabaseManager

from tests.mocks import build_ohlcv


# --------------------------------------------------------------------------- #
# Fixed-clock helper (monkeypatchable module-level `datetime`)                #
# --------------------------------------------------------------------------- #
class FakeDateTime:
    """Replaces `from datetime import datetime` inside a module."""

    def __init__(self, fixed: real_datetime):
        self._fixed = fixed

    def now(self, tz=None):
        if tz is not None:
            return tz.localize(self._fixed)
        return self._fixed

    def strptime(self, *args, **kwargs):
        return real_datetime.strptime(*args, **kwargs)


@pytest.fixture()
def fixed_clock(monkeypatch):
    fixed = real_datetime(2026, 8, 20, 14, 30)  # Thursday 14:30 UTC
    monkeypatch.setattr("api.engines.news_engine.datetime", FakeDateTime(fixed))
    monkeypatch.setattr("api.engines.market_universe.datetime", FakeDateTime(fixed))
    return fixed


# --------------------------------------------------------------------------- #
# 1. NewsEngine (offline)                                                     #
# --------------------------------------------------------------------------- #
HTML_CALENDAR = """
<table class="calendar__table">
  <tr class="calendar__row">
    <td class="calendar__date">Thu<br>\nAug 20</td>
    <td class="calendar__currency">USD</td>
    <td class="calendar__event">CPI m/m</td>
    <td class="calendar__time">8:30am</td>
    <td class="calendar__impact"><span class="impact high"></span></td>
    <td class="calendar__forecast">0.2%</td>
    <td class="calendar__previous">0.1%</td>
    <td class="calendar__actual">0.3%</td>
  </tr>
  <tr class="calendar__row">
    <td class="calendar__currency">EUR</td>
    <td class="calendar__event">GDP q/q</td>
    <td class="calendar__time">5:00am</td>
    <td class="calendar__impact"><span class="impact medium"></span></td>
  </tr>
</table>
"""


def test_economic_calendar_html_parsing():
    provider = EconomicCalendarProvider()
    events = provider._parse_html(HTML_CALENDAR)
    assert len(events) == 2
    assert events[0]["title"] == "CPI m/m"
    assert events[0]["country"] == "USD"
    assert events[0]["impact"] == "High"
    assert events[0]["date"] == "Thu Aug 20"
    assert events[0]["forecast"] == "0.2%"
    assert events[1]["impact"] == "Medium"
    assert events[1]["date"] == "Thu Aug 20"  # date carried over
    assert provider._parse_html("<html><body>nothing</body></html>") == []


def test_news_filter_high_impact():
    filt = NewsFilter()
    events = [
        {"impact": "High", "country": "USD"},
        {"impact": "High", "country": "GBP"},
        {"impact": "Medium", "country": "USD"},
        {"impact": "Low", "country": "USD"},
    ]
    assert len(filt.filter_high_impact(events)) == 2
    filtered = filt.filter_high_impact(events, asset_currency="EUR")
    assert all(e["country"] in ("USD", "EUR") for e in filtered)


def test_event_risk_engine_blocks_only_relevant_window(fixed_clock):
    # Fixed now: Thu 2026-08-20 14:30 UTC. NY 8:30am = 12:30 UTC → 2h ago
    # (beyond safety_after=60min → not blocking); 10:15am NY = 14:15 UTC
    # (15 min ago → blocking).
    engine = EventRiskEngine(NewsFilter(safety_before_mins=30, safety_after_mins=60))
    events = [
        {"impact": "High", "country": "USD", "date": "Thu Aug 20", "time": "8:30am"},
        {"impact": "High", "country": "USD", "date": "Thu Aug 20", "time": "10:15am"},
    ]
    res = engine.check_risk(events)
    assert res["is_blocked"] is True
    assert res["blocking_event"]["time"] == "10:15am"
    assert "time_utc" in res["blocking_event"]

    # Only the old event → no block
    res_old = engine.check_risk(events[:1])
    assert res_old["is_blocked"] is False


def test_session_filter_rules(fixed_clock):
    s = SessionFilter(timezone="UTC")
    crypto = s.is_trading_allowed("CRYPTO")
    assert crypto["session_ok"] is True and crypto["day_ok"] is True
    forex = s.is_trading_allowed("FOREX")
    assert forex["session_ok"] is True  # Thursday
    stocks = s.is_trading_allowed("STOCKS")
    assert stocks["session_ok"] is True  # 14:30 within 9:00-22:00


def test_session_filter_weekend_closed(monkeypatch):
    fixed = real_datetime(2026, 8, 22, 14, 30)  # Saturday
    monkeypatch.setattr("api.engines.news_engine.datetime", FakeDateTime(fixed))
    s = SessionFilter(timezone="UTC")
    assert s.is_trading_allowed("FOREX")["session_ok"] is False


async def test_news_engine_check_trading_allowed(monkeypatch, fixed_clock):
    engine = NewsEngine()

    async def fake_fetch():
        return [{"impact": "High", "country": "USD", "date": "Thu Aug 20", "time": "10:15am"}]

    monkeypatch.setattr(engine.provider, "fetch_events", fake_fetch)
    engine.set_window_mode("avoid")
    res = await engine.check_trading_allowed(asset_currency="EUR", asset_class="CRYPTO")
    assert res["trading_allowed"] is False  # blocking event in the window
    assert res["news_ok"] is False
    assert res["blocking_event"] is not None

    engine.set_window_mode("trade")
    res_trade = await engine.check_trading_allowed(asset_currency="EUR", asset_class="CRYPTO")
    assert res_trade["news_ok"] is True
    assert res_trade["trading_allowed"] is True

    async def fake_fetch_empty():
        return []

    monkeypatch.setattr(engine.provider, "fetch_events", fake_fetch_empty)
    res_empty = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert res_empty["status"] == "DATA_UNAVAILABLE"
    # P0-1: default policy block_tradfi_only — crypto keeps trading on outage
    assert res_empty["trading_allowed"] is True
    assert res_empty["news_ok"] is True

    # Explicit block_all keeps the historical fail-safe behaviour on crypto.
    engine.set_unavailable_policy("block_all")
    res_block_all = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert res_block_all["trading_allowed"] is False
    assert res_block_all["news_ok"] is False


# --------------------------------------------------------------------------- #
# 2. BacktestEngine                                                           #
# --------------------------------------------------------------------------- #
async def test_backtest_insufficient_data():
    engine = BacktestEngine(AnalysisEngine(), SignalEngine(min_score=80), RiskEngine())
    res = await engine.run_backtest("btc_usdt", pd.DataFrame(), strategy_mode="structure")
    assert "error" in res

    small = build_ohlcv(n=20)
    res2 = await engine.run_backtest("btc_usdt", small, strategy_mode="structure")
    assert "error" in res2


async def test_backtest_runs_on_trending_data():
    engine = BacktestEngine(AnalysisEngine(), SignalEngine(min_score=80), RiskEngine())
    df = build_ohlcv(n=120)
    res = await engine.run_backtest("btc_usdt", df, strategy_mode="structure")
    # Either a clean report or a no-trade report — never a crash
    assert isinstance(res, dict)
    if "error" not in res:
        for key in ("initial_balance", "final_balance", "total_pnl", "trades"):
            assert key in res, f"missing backtest key {key}"


# --------------------------------------------------------------------------- #
# 3. BrokerConnector (fake adapters)                                          #
# --------------------------------------------------------------------------- #
class FakeAdapter:
    exchange_id = "gate"
    connect_ok = True
    # v3.1 P0-2: spot-like fake — an empty get_positions() is NOT proof of close
    positions_authoritative = False

    def __init__(self, *args, **kwargs):
        self.connected = False
        self.closed = False
        self.last_order = None

    async def connect(self):
        self.connected = True
        return self.connect_ok

    async def close(self):
        self.closed = True

    async def get_balance(self, asset="USDT"):
        return 123.45

    async def execute_order(self, symbol, side, quantity, sl=None, tp=None):
        self.last_order = (symbol, side, quantity)
        return {"success": True, "broker_order_id": "ORD-1", "average": 100.0,
                "tp_order_id": "TP-1", "sl_order_id": "SL-1"}

    async def close_all_positions(self):
        return {"closed_positions": 0}

    async def close_position(self, symbol, side, quantity):
        return {"success": True}

    async def get_positions(self):
        return []


@pytest.fixture()
def connector(monkeypatch, tmp_path):
    monkeypatch.setattr("api.engines.broker_connector.CCXTAdapter", FakeAdapter)
    monkeypatch.setattr("api.engines.broker_connector.PrimeXBTAdapter", FakeAdapter)
    db = DatabaseManager(str(tmp_path / "broker.db"))
    c = BrokerConnector(db_manager=db)
    return c, db


async def test_broker_add_remove_and_status(connector):
    c, db = connector
    assert await c.add_broker("b1", "gate", "K", "S") is True
    assert c.get_status()["broker_count"] == 1
    assert "b1" in c.active_adapters

    # Failed connect → not added
    FakeAdapter.connect_ok = False
    assert await c.add_broker("b2", "binance", "K", "S") is False
    assert "b2" not in c.active_adapters
    FakeAdapter.connect_ok = True

    assert await c.remove_broker("b1") is True
    assert await c.remove_broker("b1") is False
    assert c.get_status()["broker_count"] == 0


async def test_broker_balances_with_web3_wallet(connector, monkeypatch):
    c, db = connector
    await c.add_broker("b1", "gate", "K", "S")
    c.web3_wallets["w1"] = {"provider": "METAMASK", "address": "0xabc123456789", "network": "mainnet"}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"balance": 2 * 10 ** 18}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, timeout=5.0):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    balances = await c.get_all_balances()
    assert balances["b1"]["total_usdt"] == 123.45
    assert balances["b1"]["type"] == "BROKER"
    assert balances["w1"]["balance"] == 2.0  # wei → ETH
    assert balances["w1"]["type"] == "WEB3"


async def test_broker_execute_routes_and_persists(connector):
    c, db = connector
    await c.add_broker("b1", "gate", "K", "S")
    # gate appears in btc_usdt broker_symbols
    res = await c.execute({"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
                           "sl": 99.0, "tp": 102.0, "strategy": "structure"},
                          {"quantity": 1.0, "leverage": 1.0})
    assert res["success"] is True
    assert res["position"]["mode"] == "REAL"
    assert res["position"]["metadata"]["broker_id"] == "b1"
    assert db.get_active_positions("REAL")[0]["symbol"] == "btc_usdt"

    # No route for an unknown market
    res_unknown = await c.execute({"market_id": "__nope__", "direction": "BUY"},
                                  {"quantity": 1.0, "leverage": 1.0})
    assert res_unknown["success"] is False
    assert "UNSUPPORTED_SYMBOL" in res_unknown["reason"]


async def test_broker_execute_without_connection(connector):
    c, db = connector
    res = await c.execute({"market_id": "btc_usdt", "direction": "BUY"},
                          {"quantity": 1.0, "leverage": 1.0})
    assert res["success"] is False and res["reason"] == "NO_BROKER_CONNECTED"


async def test_broker_emergency_and_mode(connector):
    c, db = connector
    # set_mode: REAL blocked without brokers, DEMO always fine
    ok, msg = await c.set_mode("REAL")
    assert ok is False and "No active broker" in msg
    ok, msg = await c.set_mode("DEMO")
    assert ok is True

    await c.add_broker("b1", "gate", "K", "S")
    ok, msg = await c.set_mode("REAL")
    assert ok is True

    assert c.trigger_emergency_stop() is True
    ok, msg = await c.set_mode("REAL")
    assert ok is False and "Emergency Stop" in msg
    assert c.reset_emergency_stop() is True

    closed = await c.close_all_positions()
    assert closed["b1"]["closed_positions"] == 0
    await c.shutdown()
    assert c.get_status()["broker_count"] == 0


async def test_broker_reconcile_positions(connector):
    c, db = connector
    await c.add_broker("b1", "gate", "K", "S")
    db.save_trade({"id": "R1", "mode": "REAL", "symbol": "btc_usdt",
                   "display_symbol": "BTC/USDT",
                   "direction": "BUY", "entry_price": 100.0, "quantity": 1.0,
                   "sl": 99.0, "tp": 102.0, "status": "OPEN", "pnl": 0.0,
                   "metadata": {"broker_id": "b1", "broker_symbol": "BTC/USDT"}})
    # v3.1 P0-2: the fake adapter is NOT positions-authoritative (spot-like),
    # so an empty get_positions() must NOT close the DB trade.
    closed = await c.reconcile_positions()
    assert closed == []
    assert db.get_active_positions("REAL")[0]["id"] == "R1"


async def test_broker_reconcile_closes_when_authoritative(connector):
    c, db = connector
    await c.add_broker("b1", "gate", "K", "S")
    db.save_trade({"id": "R2", "mode": "REAL", "symbol": "btc_usdt",
                   "display_symbol": "BTC/USDT",
                   "direction": "BUY", "entry_price": 100.0, "quantity": 1.0,
                   "sl": 99.0, "tp": 102.0, "status": "OPEN", "pnl": 0.0,
                   "metadata": {"broker_id": "b1", "broker_symbol": "BTC/USDT"}})
    # Authoritative adapter (derivatives with fetchPositions) reporting [] →
    # the position really disappeared on the broker → CLOSE in DB.
    c.active_adapters["b1"].positions_authoritative = True
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    assert closed[0]["metadata"]["close_reason"] == "BROKER_RECONCILED_CLOSE"
    assert db.get_active_positions("REAL") == []

    # Empty DB → nothing to reconcile
    assert await c.reconcile_positions() == []


async def test_broker_initialize_from_db(connector):
    c, db = connector
    db.save_broker_config("b1", "gate", "K", "S", None)
    db.save_wallet("w1", "METAMASK", "0xabc123456789", "mainnet")
    await c.initialize_from_db()
    assert "b1" in c.active_adapters
    assert "w1" in c.web3_wallets


# --------------------------------------------------------------------------- #
# 4. NotificationEngine (fake httpx)                                          #
# --------------------------------------------------------------------------- #
async def test_notification_disabled_without_credentials():
    engine = NotificationEngine(telegram_token=None, telegram_chat_id=None)
    engine.discord_webhook = None
    await engine.send_telegram("hello")  # silently skipped
    await engine.send_discord("hello")   # silently skipped


async def test_notification_sends(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json=None, timeout=None):
            calls.append((url, json))
            return FakeResponse()

    monkeypatch.setattr("api.engines.notification_engine.httpx",
                        type("H", (), {"AsyncClient": FakeClient}))
    engine = NotificationEngine(telegram_token="tok", telegram_chat_id="123")
    engine.discord_webhook = "https://discord.example/webhook"
    await engine.send_telegram("hi")
    await engine.send_discord("hi")
    assert len(calls) == 2
    assert "api.telegram.org" in calls[0][0]
    assert "discord.example" in calls[1][0]

    # notify() dispatches every event type without crashing
    for event in ("SIGNAL", "ORDER_OPEN", "ORDER_CLOSE", "EMERGENCY_STOP", "DAILY_REPORT"):
        await engine.notify(event, {"symbol": "BTC/USDT", "direction": "BUY"})


def test_notification_update_config():
    engine = NotificationEngine()
    engine.update_config("new-token", "new-chat")
    assert engine.enabled is True


# --------------------------------------------------------------------------- #
# 5. YahooProvider (fake yfinance)                                            #
# --------------------------------------------------------------------------- #
class FakeYfTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period="1d", interval="1m"):
        idx = pd.date_range("2026-08-20 09:30", periods=5, freq="1min")
        return pd.DataFrame(
            {"Open": [100.0] * 5, "High": [101.0] * 5, "Low": [99.0] * 5,
             "Close": [100.0 + i for i in range(5)], "Volume": [100.0] * 5},
            index=idx)


class FakeYfModule:
    @staticmethod
    def Ticker(symbol):
        return FakeYfTicker(symbol)


async def test_yahoo_provider_quote_ohlcv_health(monkeypatch):
    monkeypatch.setattr("api.engines.data_providers.yahoo_provider.yf", FakeYfModule())
    provider = YahooProvider("FOREX")

    quote = await provider.get_quote("EURUSD=X")
    assert quote is not None
    assert quote.last == 104.0
    assert quote.status == "DELAYED"  # Yahoo is explicitly NOT realtime
    assert quote.volume == 100.0

    df = await provider.get_ohlcv("EURUSD=X", "1m", 50)
    assert not df.empty
    assert list(df.columns)[:6] == ["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 5

    health = await provider.health_check()
    assert health["status"] == "ONLINE"
    assert await provider.get_symbols() == []


# --------------------------------------------------------------------------- #
# 6. RiskEngine lifecycle extras                                             #
# --------------------------------------------------------------------------- #
def test_risk_engine_peak_drawdown_and_safety():
    risk = RiskEngine(max_risk_pct=1.0, max_daily_loss_pct=3.0, max_drawdown_pct=5.0)
    risk.update_peak(1000.0)
    assert risk.peak_balance == 1000.0
    assert risk.get_current_drawdown_pct(950.0) == pytest.approx(5.0)
    fresh = RiskEngine()
    assert fresh.get_current_drawdown_pct(0.0) == 0.0  # peak unset fallback

    assert risk.check_global_safety(1000.0, 0.0)["safe"] is True
    drawdown = risk.check_global_safety(900.0, 0.0)
    assert drawdown["safe"] is False and "Drawdown" in drawdown["reason"]
    daily = risk.check_global_safety(1000.0, -100.0)
    assert daily["safe"] is False and "Daily Loss" in daily["reason"]


def test_risk_engine_register_closed_trade_and_cool_down():
    risk = RiskEngine(cool_down_mins=30)
    risk.register_closed_trade(10.0)
    assert risk.daily_pnl == 10.0
    assert risk.last_loss_time is None
    risk.register_closed_trade(-5.0)
    assert risk.last_loss_time is not None
    # Cool-down now active
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is False
    assert "Cool-down" in res["reason"]


def test_risk_engine_apply_settings_and_persisted_peak():
    risk = RiskEngine()
    risk.apply_settings({"max_risk_pct": "2.5", "max_leverage": "10",
                         "cool_down_mins": "15", "max_open_positions": "3",
                         "emergency_stop_drawdown_pct": "8", "peak_balance": "1250"})
    assert risk.max_risk_pct == 2.5
    assert risk.max_leverage == 10
    assert risk.cool_down_mins == 15
    assert risk.max_open_positions == 3
    assert risk.max_drawdown_pct == 8
    assert risk.peak_balance == 1250
    # Invalid values are ignored, not fatal
    risk.apply_settings({"max_risk_pct": "not-a-number"})
    assert risk.max_risk_pct == 2.5


# --------------------------------------------------------------------------- #
# 7. MarketUniverse session logic with a fixed clock                          #
# --------------------------------------------------------------------------- #
def test_market_universe_sessions_fixed_clock(fixed_clock):
    u = MarketUniverse()
    # Thursday 14:30 UTC
    assert u.get_market_status("btc_usdt") == "OPEN"  # crypto 24/7
    assert u.get_market_status("eur_usd") == "OPEN"   # forex weekday
    # STOCKS/INDICES: US markets (9:30–16:00 NY = 13:30–20:00 UTC in Aug)
    assert u.get_market_status("aapl") == "OPEN"
    # Commodities: open outside the 17h break and weekends
    assert u.get_market_status("gold") == "OPEN"


def test_market_universe_weekend_sessions(monkeypatch):
    fixed = real_datetime(2026, 8, 22, 14, 30)  # Saturday
    monkeypatch.setattr("api.engines.market_universe.datetime", FakeDateTime(fixed))
    u = MarketUniverse()
    assert u.get_market_status("eur_usd") == "CLOSED"   # forex weekend
    assert u.get_market_status("aapl") == "CLOSED"      # stocks weekend
    assert u.get_market_status("gold") == "CLOSED"      # commodities weekend
    assert u.get_market_status("btc_usdt") == "OPEN"    # crypto never closes
