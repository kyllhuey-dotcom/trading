"""Regression and function-level tests for remaining engine public methods."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from api.engines.backtest_engine import BacktestEngine
from api.engines.broker_adapters.base_adapter import BrokerAdapter
from api.engines.broker_connector import BrokerConnector
from api.engines.data_engine import DataEngine
from api.engines.data_layer import DataLayer
from api.engines.data_providers.base_provider import MarketDataProvider
from api.engines.db_manager import DatabaseManager
from api.engines.exchange_constraints import normalize_order
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.risk_engine import RiskEngine
from api.engines.strategies.base_strategy import BaseStrategy


class OneShotSignal:
    def __init__(self):
        self.calls = 0
        self.timestamps = None

    def generate_signal(self, analysis, news, df, **kwargs):
        self.calls += 1
        self.timestamps = list(df["Timestamp"])
        if self.calls == 1:
            return {
                "status": "SIGNAL_DETECTED",
                "direction": "BUY",
                "entry": 100.0,
                "sl": 90.0,
                "tp": 110.0,
            }
        return {"status": "NO_TRADE"}


class StaticAnalysis:
    def identify_structure(self, df):
        return {"status": "VALID", "trend": "BULLISH"}


async def test_backtest_intrabar_conservative_exit_and_isolated_risk_state():
    signal = OneShotSignal()
    live_risk = RiskEngine(max_risk_pct=1, max_leverage=20, cool_down_mins=0)
    live_risk.consecutive_losses = 2
    live_risk.peak_balance = 50_000
    engine = BacktestEngine(StaticAnalysis(), signal, live_risk)
    index = pd.date_range("2026-01-01", periods=52, freq="min")
    frame = pd.DataFrame(
        {
            "High": [101.0] * 51 + [120.0],
            "Low": [99.0] * 51 + [80.0],
            "Close": [100.0] * 52,
            "Volume": [10.0] * 52,
        },
        index=index,
    )
    result = await engine.run_backtest("btc_usdt", frame)
    assert result["total_trades"] == 1
    assert result["trades"][0]["exit"] == 90.0  # both touched: conservative SL
    assert result["trades"][0]["pnl"] < 0
    assert result["open_position"] is None
    assert len(set(signal.timestamps)) == len(signal.timestamps)
    assert live_risk.consecutive_losses == 2
    assert live_risk.peak_balance == 50_000


async def test_backtest_validates_schema_and_balance():
    engine = BacktestEngine(StaticAnalysis(), OneShotSignal(), RiskEngine())
    missing = pd.DataFrame({"Close": [1.0] * 50})
    assert "Missing OHLCV columns" in (await engine.run_backtest("x", missing))["error"]
    valid = pd.DataFrame({"High": [2.0] * 50, "Low": [1.0] * 50, "Close": [1.5] * 50})
    for value in (0, -1, float("nan"), "bad"):
        result = await engine.run_backtest("x", valid, initial_balance=value)
        assert "positive finite" in result["error"]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"quantity": "bad", "entry": 100}, "numeric"),
        ({"quantity": 1, "entry": float("inf")}, "finite"),
        ({"quantity": 1, "entry": 100, "direction": "hold"}, "BUY or SELL"),
        ({"quantity": 0, "entry": 100}, "positive"),
        ({"quantity": 1, "entry": -1}, "prices must be positive"),
        ({"quantity": 1, "entry": 100, "sl": 101}, "Stop loss"),
        ({"quantity": 1, "entry": 100, "tp": 99}, "Take profit"),
    ],
)
def test_normalize_order_rejects_unsafe_values(kwargs, reason):
    result = normalize_order(**kwargs)
    assert result["allowed"] is False
    assert reason in result["reason"]


def test_normalize_order_revalidates_levels_after_tick_rounding():
    result = normalize_order(
        1,
        100.004,
        "BUY",
        sl=99.999,
        tp=100.005,
        info={"tick_size": 0.01},
    )
    assert result["allowed"] is False
    assert "after rounding" in result["reason"]


class Subscriber:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    async def broadcast(self, message):
        if self.fail:
            raise RuntimeError("dead socket")
        self.messages.append(json.loads(message))


async def test_data_layer_broadcast_and_health_are_failure_isolated():
    layer = DataLayer()
    good_subscriber = Subscriber()
    layer.subscribers = [object(), Subscriber(fail=True), good_subscriber]
    await layer.broadcast_update({"price": 100})
    assert good_subscriber.messages == [{"price": 100}]

    class Healthy:
        async def health_check(self):
            return {"provider": "healthy", "status": "ONLINE"}

    class Invalid:
        async def health_check(self):
            return "not-a-dict"

    class Broken:
        async def health_check(self):
            raise RuntimeError("offline")

    class Slow:
        async def health_check(self):
            await asyncio.sleep(1)

    layer.providers = {
        "healthy": Healthy(),
        "invalid": Invalid(),
        "broken": Broken(),
        "slow": Slow(),
    }
    layer.provider_timeout_s = 0.001
    report = await layer.get_health()
    by_provider = {row["provider"]: row for row in report}
    assert by_provider["healthy"]["status"] == "ONLINE"
    assert by_provider["invalid"]["status"] == "ERROR"
    assert by_provider["broken"]["status"] == "OFFLINE"
    assert by_provider["slow"]["status"] == "OFFLINE"


async def test_data_engine_broadcast_market_update_paths():
    engine = object.__new__(DataEngine)
    engine.universe = SimpleNamespace(
        get_info=lambda market_id: (
            {"display_symbol": "BTC/USDT", "providers": {"gate": "BTC/USDT"}}
            if market_id == "btc_usdt" else None
        )
    )
    engine.layer = SimpleNamespace(broadcast_update=AsyncMock())
    engine.fetch_ticker = AsyncMock(return_value={
        "last": 100,
        "status": "LIVE",
        "timestamp": int(__import__("time").time() * 1000),
        "change_24h": 1,
        "volume": 10,
        "source": "gate",
    })
    assert await engine.broadcast_market_update("unknown") is None
    await engine.broadcast_market_update("btc_usdt")
    update = engine.layer.broadcast_update.await_args.args[0]
    assert update["type"] == "MARKET_UPDATE"
    assert update["data_age_ms"] >= 0
    assert update["data_age_ms"] <= 5
    assert update["realtime_source"] is True

    engine.fetch_ticker = AsyncMock(return_value=None)
    engine.layer.broadcast_update.reset_mock()
    await engine.broadcast_market_update("btc_usdt")
    engine.layer.broadcast_update.assert_not_awaited()


async def test_broker_reconciliation_does_not_close_on_provider_failure(tmp_path):
    db = DatabaseManager(str(tmp_path / "reconcile.db"))
    db.save_trade({
        "id": "REAL-1",
        "mode": "REAL",
        "symbol": "btc_usdt",
        "display_symbol": "BTC/USDT",
        "direction": "BUY",
        "entry_price": 100,
        "quantity": 1,
        "sl": 90,
        "tp": 120,
        "status": "OPEN",
        "pnl": 0,
        "metadata": {"broker_id": "broken", "broker_symbol": "BTC/USDT"},
    })
    connector = BrokerConnector(db)
    connector.set_db_manager(db)
    connector.active_adapters["broken"] = SimpleNamespace(
        get_positions=AsyncMock(side_effect=RuntimeError("temporary outage"))
    )
    assert await connector.reconcile_positions() == []
    assert db.get_active_positions("REAL")[0]["id"] == "REAL-1"


async def test_broker_replacement_closes_previous_adapter(monkeypatch):
    connector = BrokerConnector()
    previous = SimpleNamespace(close=AsyncMock())
    connector.active_adapters["main"] = previous

    class Replacement:
        exchange_id = "gate"

        def __init__(self, *args):
            self.close = AsyncMock()

        async def connect(self):
            return True

    monkeypatch.setattr("api.engines.broker_connector.CCXTAdapter", Replacement)
    assert await connector.add_broker("main", "gate", "key", "secret") is True
    previous.close.assert_awaited_once()
    assert isinstance(connector.active_adapters["main"], Replacement)


def test_portfolio_history_proxies_and_db_delete_history(tmp_path):
    db = DatabaseManager(str(tmp_path / "portfolio.db"))
    portfolio = PortfolioEngine(db)
    trade = {
        "id": "T-1", "mode": "DEMO", "symbol": "btc_usdt",
        "direction": "BUY", "entry_price": 100, "quantity": 1,
        "status": "CLOSED", "pnl": 5, "close_time": "2026-08-22T12:00:00",
    }
    db.save_trade(trade)
    assert portfolio.history[0]["id"] == "T-1"
    assert portfolio.get_history("DEMO", 1)[0]["id"] == "T-1"
    db.delete_history("DEMO")
    assert portfolio.history == []

    db.save_trade({**trade, "id": "T-2"})
    portfolio.reset_history("DEMO")
    assert portfolio.history == []


async def test_abstract_interface_default_bodies_are_safe():
    # Invoke unbound defaults explicitly: abstract classes cannot be instantiated,
    # but their fallback bodies are still part of the tested API contract.
    assert await MarketDataProvider.get_symbols(object()) is None
    assert await MarketDataProvider.get_quote(object(), "x") is None
    assert await MarketDataProvider.get_ohlcv(object(), "x", "1m") is None
    assert await MarketDataProvider.health_check(object()) is None
    assert await MarketDataProvider.get_order_book(object(), "x") is None
    assert await MarketDataProvider.get_recent_trades(object(), "x") is None
    assert BaseStrategy.generate_signal(object(), "x", pd.DataFrame()) is None

    assert await BrokerAdapter.connect(object()) is None
    assert await BrokerAdapter.get_balance(object()) is None
    assert await BrokerAdapter.get_positions(object()) is None
    assert await BrokerAdapter.execute_order(object(), "x", "buy", 1) is None
    assert await BrokerAdapter.close_all_positions(object()) is None
    assert await BrokerAdapter.cancel_order(object(), "one") is None
    assert BrokerAdapter.get_status(object()) is None
    assert await BrokerAdapter.close(object()) is None
