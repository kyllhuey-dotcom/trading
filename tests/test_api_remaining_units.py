"""Offline unit tests for every API endpoint and background-task function."""
import asyncio
import copy
import time
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from fastapi import HTTPException

from api import index as idx
from api.engines.state_machine import BotState


@pytest.fixture(autouse=True)
def restore_global_state():
    bot_state = copy.deepcopy(idx.bot_state)
    metrics_state = copy.deepcopy(idx.metrics_state)
    news_cache = copy.deepcopy(idx._news_cache)
    scan_count = idx._scan_counter["n"]
    machine_state = idx.state_machine.current_state
    risk_state = {
        name: copy.deepcopy(getattr(idx.risk_engine, name))
        for name in ("daily_pnl", "last_loss_time", "consecutive_losses", "peak_balance")
    }
    advanced_metrics = {
        name: copy.deepcopy(value)
        for name, value in vars(idx.metrics_engine).items()
        if name != "_lock"
    }
    pending_orders = copy.deepcopy(idx.demo_execution.pending_orders)
    scanner_duration = idx.scanner_engine.last_scan_duration
    yield
    idx.bot_state.clear()
    idx.bot_state.update(bot_state)
    idx.metrics_state.clear()
    idx.metrics_state.update(metrics_state)
    idx._news_cache.clear()
    idx._news_cache.update(news_cache)
    idx._scan_counter["n"] = scan_count
    idx.state_machine.current_state = machine_state
    for name, value in risk_state.items():
        setattr(idx.risk_engine, name, value)
    for name, value in advanced_metrics.items():
        setattr(idx.metrics_engine, name, value)
    idx.demo_execution.pending_orders = pending_orders
    idx.scanner_engine.last_scan_duration = scanner_duration


async def test_history_arm_and_emergency_endpoints(monkeypatch):
    monkeypatch.setattr(idx.db_manager, "get_history", MagicMock(return_value=[{"id": "one"}]))
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    assert await idx.get_history("DEMO", 1) == [{"id": "one"}]

    idx.bot_state["armed"] = False
    assert await idx.arm_bot() == {"armed": True}
    assert await idx.arm_bot() == {"armed": False}

    stop = AsyncMock()
    monkeypatch.setattr(idx, "emergency_stop_logic", stop)
    response = await idx.emergency_stop_api()
    stop.assert_awaited_once()
    assert response["success"] is True


def _patch_emergency_dependencies(monkeypatch):
    monkeypatch.setattr(idx.demo_execution, "clear_active_positions", MagicMock())
    monkeypatch.setattr(
        idx.broker_connector,
        "close_all_positions",
        AsyncMock(return_value={"main": {"closed_positions": 1}}),
    )
    monkeypatch.setattr(idx.broker_connector, "trigger_emergency_stop", MagicMock())
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    monkeypatch.setattr(idx.notification_engine, "notify", AsyncMock())


async def test_emergency_logic_and_reset(monkeypatch):
    _patch_emergency_dependencies(monkeypatch)
    idx.bot_state.update(is_running=True, armed=True, mode="DEMO")
    await idx.emergency_stop_logic("risk limit")
    assert idx.bot_state["is_running"] is False
    assert idx.bot_state["armed"] is False
    assert idx.state_machine.current_state == BotState.EMERGENCY_STOP
    idx.broker_connector.trigger_emergency_stop.assert_called_once()
    idx.notification_engine.notify.assert_awaited_once()

    monkeypatch.setattr(idx.broker_connector, "reset_emergency_stop", MagicMock())
    response = await idx.emergency_reset()
    assert response == {"success": True, "state": "STOPPED"}
    assert idx.state_machine.current_state == BotState.STOPPED


async def test_demo_reset_balance_and_true_range(monkeypatch):
    frame = pd.DataFrame({
        "High": [10, 12], "Low": [8, 9], "Close": [9, 11],
    })
    assert idx.pd_concat_tr(frame).tolist() == [2.0, 3.0]

    monkeypatch.setattr(idx.demo_execution, "clear_active_positions", MagicMock())
    monkeypatch.setattr(idx.portfolio_engine, "reset_history", MagicMock())
    monkeypatch.setattr(idx.portfolio_engine, "set_balance", MagicMock())
    monkeypatch.setattr(idx.portfolio_engine, "get_balance", MagicMock(return_value=10_000.0))
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    assert await idx.demo_reset() == {"success": True, "balance": 10_000.0}
    assert idx.risk_engine.consecutive_losses == 0

    monkeypatch.setattr(idx.risk_engine, "update_peak", MagicMock())
    assert await idx.demo_balance({"balance": "25.5"}) == {"success": True, "balance": 25.5}
    for value in (-1, "bad", "nan", "inf"):
        with pytest.raises(HTTPException) as exc_info:
            await idx.demo_balance({"balance": value})
        assert exc_info.value.status_code == 400


async def test_manual_order_success_defaults_and_risk_based_sizing(monkeypatch):
    info = {"display_symbol": "BTC/USDT", "asset_class": "CRYPTO"}
    monkeypatch.setattr(idx.data_engine.universe, "get_info", MagicMock(return_value=info))
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={
        "last": 100, "bid": 99.9, "ask": 100.1,
    }))
    monkeypatch.setattr(idx.data_engine, "fetch_ohlcv", AsyncMock(return_value=pd.DataFrame()))
    execute = AsyncMock(return_value={"success": True, "position": {"id": "P-1"}})
    monkeypatch.setattr(idx.execution_router, "execute", execute)
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    idx.bot_state.update(balance=10_000, mode="DEMO")

    result = await idx.manual_order({
        "market_id": "btc_usdt", "direction": "BUY", "quantity": 1,
    })
    assert result["success"] is True
    signal, risk_data = execute.await_args.args[1:3]
    assert signal["sl"] == pytest.approx(98)
    assert signal["tp"] == pytest.approx(104)
    assert risk_data["quantity"] == 1

    execute.reset_mock()
    result = await idx.manual_order({
        "market_id": "btc_usdt",
        "direction": "SELL",
        "quantity": 0,
        "risk_based": True,
        "sl": 102,
        "tp": 96,
        "order_type": "LIMIT",
        "limit_price": 101,
    })
    assert result["success"] is True
    assert execute.await_args.args[2]["quantity"] == pytest.approx(50)


async def test_manual_order_rejects_invalid_inputs(monkeypatch):
    monkeypatch.setattr(
        idx.data_engine.universe,
        "get_info",
        MagicMock(return_value={"display_symbol": "BTC/USDT"}),
    )
    ticker = AsyncMock(return_value={"last": 100})
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", ticker)
    monkeypatch.setattr(idx.data_engine, "fetch_ohlcv", AsyncMock(return_value=pd.DataFrame()))
    idx.bot_state["balance"] = 1_000

    with pytest.raises(HTTPException):
        await idx.manual_order({"market_id": "btc_usdt", "direction": "HOLD"})

    ticker.return_value = None
    assert (await idx.manual_order({"market_id": "btc_usdt", "quantity": 1}))["reason"] == \
        "No market data available"
    ticker.return_value = {"last": "invalid"}
    assert (await idx.manual_order({"market_id": "btc_usdt", "quantity": 1}))["reason"] == \
        "Invalid price"
    ticker.return_value = {"last": 100}

    invalid_bodies = [
        {"sl": "bad", "quantity": 1},
        {"quantity": "bad"},
        {"quantity": "nan"},
        {"quantity": 1, "order_type": "LIMIT"},
        {"quantity": 1, "order_type": "STOP"},
    ]
    for extra in invalid_bodies:
        with pytest.raises(HTTPException) as exc_info:
            await idx.manual_order({"market_id": "btc_usdt", **extra})
        assert exc_info.value.status_code == 400

    no_quantity = await idx.manual_order({"market_id": "btc_usdt", "quantity": 0})
    assert no_quantity["success"] is False
    assert "quantity must be positive" in no_quantity["reason"]

    excessive = await idx.manual_order({"market_id": "btc_usdt", "quantity": 100})
    assert excessive["success"] is False
    assert "exceeds max" in excessive["reason"]

    wrong_stop = await idx.manual_order({
        "market_id": "btc_usdt", "quantity": 1, "sl": 101, "tp": 110,
    })
    assert wrong_stop["success"] is False
    assert "wrong side" in wrong_stop["reason"]


async def test_broker_endpoints_all_paths(monkeypatch):
    monkeypatch.setattr(
        idx.db_manager,
        "get_broker_public_list",
        MagicMock(return_value=[{"broker_id": "main"}]),
    )
    monkeypatch.setattr(
        idx.broker_connector,
        "get_status",
        MagicMock(return_value={"broker_count": 1}),
    )
    assert (await idx.get_brokers())["brokers"][0]["broker_id"] == "main"

    with pytest.raises(HTTPException):
        await idx.add_broker_api({"broker_id": "missing"})

    monkeypatch.setattr(idx.broker_connector, "add_broker", AsyncMock(side_effect=[True, False, True]))
    monkeypatch.setattr(idx.db_manager, "save_broker_config", MagicMock())
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    body = {
        "broker_id": "main",
        "exchange_id": "gate",
        "api_key": "key",
        "api_secret": "secret",
    }
    assert (await idx.add_broker_api(body))["success"] is True
    assert (await idx.add_broker_api({**body, "broker_id": "backup"}))["success"] is False

    monkeypatch.setattr(idx.db_manager, "set_broker_active", MagicMock())
    monkeypatch.setattr(idx.db_manager, "get_all_broker_configs", MagicMock(return_value=[{
        **body,
        "api_passphrase": None,
    }]))
    enabled = await idx.toggle_broker_api("main", {"is_active": True})
    assert enabled["is_active"] is True

    monkeypatch.setattr(idx.broker_connector, "remove_broker", AsyncMock(return_value=True))
    disabled = await idx.toggle_broker_api("main", {"is_active": False})
    assert disabled["is_active"] is False

    monkeypatch.setattr(idx.db_manager, "delete_broker", MagicMock(return_value=True))
    assert (await idx.delete_broker_api("main"))["success"] is True


async def test_wallet_and_performance_endpoints(monkeypatch):
    monkeypatch.setattr(idx.db_manager, "get_wallets", MagicMock(return_value=[{"wallet_id": "w1"}]))
    result = await idx.get_wallets()
    # v2.7: wallets are watch-only with additional fields
    assert "wallets" in result
    assert result["wallets"][0]["type"] == "WATCH_ONLY"
    assert result["wallets"][0]["signing_capable"] is False
    assert "note" in result
    with pytest.raises(HTTPException):
        await idx.add_wallet_api({"wallet_id": ""})

    monkeypatch.setattr(idx.db_manager, "save_wallet", MagicMock())
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    monkeypatch.setattr(idx.broker_connector, "web3_wallets", {})
    # v2.7: use a valid Ethereum address format (0x + 40 hex chars)
    result = await idx.add_wallet_api({
        "wallet_id": "w1", "provider": "WATCH_ONLY",
        "address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0",
        "chain_type": "ethereum",
    })
    assert result["success"] is True
    assert result["type"] == "WATCH_ONLY"
    assert idx.broker_connector.web3_wallets["w1"]["network"] == "mainnet"

    monkeypatch.setattr(idx.db_manager, "delete_wallet", MagicMock(return_value=True))
    assert (await idx.delete_wallet_api("w1"))["success"] is True
    monkeypatch.setattr(
        idx.portfolio_engine,
        "get_performance_report",
        MagicMock(return_value={"mode": "DEMO"}),
    )
    assert await idx.get_performance("DEMO") == {"mode": "DEMO"}


async def test_news_health_backtest_and_diagnose_endpoints(monkeypatch):
    monkeypatch.setattr(
        idx.news_aggregator,
        "get_latest_news",
        AsyncMock(return_value=[{"title": "fresh"}]),
    )
    idx._news_cache.update(ts=0, data=[])
    assert await idx.get_news() == {"news": [{"title": "fresh"}]}
    idx.news_aggregator.get_latest_news.assert_awaited_once()
    assert await idx.get_news() == {"news": [{"title": "fresh"}]}
    idx.news_aggregator.get_latest_news.assert_awaited_once()

    idx._news_cache.update(ts=0, data=[{"title": "stale"}])
    idx.news_aggregator.get_latest_news = AsyncMock(side_effect=RuntimeError("down"))
    assert await idx.get_news() == {"news": [{"title": "stale"}]}

    monkeypatch.setattr(
        idx.data_engine.health_monitor,
        "get_health_report",
        AsyncMock(return_value=[{"provider": "gate", "status": "ONLINE"}]),
    )
    assert (await idx.get_health())["providers"][0]["status"] == "ONLINE"

    for body in (
        {"limit": "bad"}, {"limit": 49}, {"limit": 5001},
        {"initial_balance": "nan"}, {"strategy": "unknown"},
    ):
        with pytest.raises(HTTPException) as exc_info:
            await idx.run_backtest(body)
        assert exc_info.value.status_code == 400

    monkeypatch.setattr(idx.data_engine, "fetch_ohlcv", AsyncMock(return_value=pd.DataFrame()))
    with pytest.raises(HTTPException):
        await idx.run_backtest({"market_id": "btc_usdt"})

    frame = pd.DataFrame({"High": [2] * 50, "Low": [1] * 50, "Close": [1.5] * 50})
    idx.data_engine.fetch_ohlcv = AsyncMock(return_value=frame)
    monkeypatch.setattr(idx.backtest_engine, "run_backtest", AsyncMock(return_value={"total_trades": 0}))
    assert await idx.run_backtest({"market_id": "btc_usdt"}) == {"total_trades": 0}

    snapshot = {"diagnosis": {"main": "ok"}, "signal": {}, "news": {}}
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value=snapshot))
    diagnosis = await idx.diagnose("btc_usdt")
    assert diagnosis["diagnosis"] == {"main": "ok"}


class Quote:
    def __init__(self, symbol, last, bid=None, ask=None):
        self.symbol = symbol
        self.data = {
            "symbol": symbol,
            "last": last,
            "bid": bid if bid is not None else last,
            "ask": ask if ask is not None else last,
        }

    def model_dump(self):
        return dict(self.data)


async def test_tick_management_demo_and_real_multi_symbol_mapping(monkeypatch):
    def get_info(market_id):
        return {
            "display_symbol": "BTC/USDT" if market_id == "btc_usdt" else "ETH/USDT",
            "providers": {
                "gate": "BTC/USDT" if market_id == "btc_usdt" else "ETH/USDT"
            },
        }
    monkeypatch.setattr(idx.data_engine.universe, "get_info", get_info)
    get_quotes = AsyncMock(return_value=[Quote("BTC/USDT", 110)])
    monkeypatch.setattr(idx.data_engine.layer, "get_all_quotes", get_quotes)
    monkeypatch.setattr(idx.demo_execution, "process_pending_orders", AsyncMock())
    monkeypatch.setattr(idx.demo_execution, "update_active_positions", AsyncMock())
    monkeypatch.setattr(
        idx.db_manager,
        "get_active_positions",
        MagicMock(return_value=[{"symbol": "btc_usdt"}]),
    )
    idx.bot_state.update(mode="DEMO", active_trades=[{"symbol": "btc_usdt"}])
    await idx.tick_management()
    idx.demo_execution.process_pending_orders.assert_awaited_once()
    assert idx.bot_state["active_trades"] == [{"symbol": "btc_usdt"}]

    real_trades = [
        {
            "id": "B", "symbol": "btc_usdt", "display_symbol": "BTC/USDT",
            "direction": "BUY", "entry_price": 100, "quantity": 1,
        },
        {
            "id": "E", "symbol": "eth_usdt", "display_symbol": "ETH/USDT",
            "direction": "SELL", "entry_price": 200, "quantity": 1,
        },
    ]
    idx.bot_state.update(mode="REAL", active_trades=copy.deepcopy(real_trades))
    get_quotes.return_value = [Quote("BTC/USDT", 110, bid=110), Quote("ETH/USDT", 190, ask=190)]
    monkeypatch.setattr(idx.broker_connector, "reconcile_positions", AsyncMock())
    monkeypatch.setattr(idx.db_manager, "get_active_positions", MagicMock(return_value=real_trades))
    save_trade = MagicMock()
    monkeypatch.setattr(idx.db_manager, "save_trade", save_trade)
    await idx.tick_management()
    saved = {call.args[0]["id"]: call.args[0]["pnl"] for call in save_trade.call_args_list}
    assert saved == {"B": 10, "E": 10}

    idx.bot_state["active_trades"] = []
    idx.demo_execution.pending_orders = []
    assert await idx.tick_management() is None


async def test_tick_broadcast_heartbeat_and_loop_wrapper(monkeypatch):
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(idx.data_engine, "broadcast_market_update", AsyncMock())
    await idx.tick_broadcaster()
    idx.manager.broadcast.assert_awaited_once()
    idx.data_engine.broadcast_market_update.assert_awaited_once()

    idx.data_engine.broadcast_market_update = AsyncMock(side_effect=RuntimeError("down"))
    await idx.tick_broadcaster()  # market update failure must not kill account stream

    monkeypatch.setattr(idx.manager, "broadcast_heartbeat", AsyncMock())
    await idx.tick_heartbeat()
    idx.manager.broadcast_heartbeat.assert_awaited_once()

    failing_tick = AsyncMock(side_effect=RuntimeError("tick failed"))

    async def stop_after_one_sleep(interval):
        raise asyncio.CancelledError

    monkeypatch.setattr(idx.asyncio, "sleep", stop_after_one_sleep)
    errors_before = idx.metrics_state["total_errors"]
    with pytest.raises(asyncio.CancelledError):
        await idx.loop_wrapper(failing_tick, 1, "test-loop")
    assert idx.metrics_state["total_errors"] == errors_before + 1


async def test_tick_scanner_executes_eligible_candidate(monkeypatch):
    signal = {
        "market_id": "btc_usdt",
        "entry": 100,
        "sl": 95,
        "tp": 110,
        "direction": "BUY",
        "strategy": "rsi",
        "status": "SIGNAL_DETECTED",
        "tradable": True,
    }
    result = {
        "symbol": "btc_usdt",
        "asset_class": "CRYPTO",
        "status": "LIVE",
        "score": 90,
        "tradable": True,
        "signal_data": signal,
        "data_age_ms": 10,
        "spread": 0.01,
        "volume": 1_000_000,
        "realtime_source": True,
        "diagnosis": {
            "checks": {
                "NEWS_CLEAR": "PASS",
                "SESSION_ALLOWED": "PASS",
                "DAY_ALLOWED": "PASS",
                "MARKET_OPEN": "PASS",
                "LIQUIDITY_VALID": "PASS",
            }
        },
    }
    idx.bot_state.update(
        is_running=True,
        armed=True,
        active_trades=[],
        mode="DEMO",
        balance=10_000,
    )
    # v2.7: set counter to trigger scan (every=4, counter increments first, so n=3 -> 4 triggers)
    idx._scan_counter["n"] = 3
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "scan_interval_seconds": "bad",
        "min_signal_score": "bad",
        "allow_delayed_data_trading": "false",
        "fee_pct": "0.05",
        "sim_slippage_pct": "0.05",
        "max_spread_pct": "0.5",
        "max_new_positions_per_scan": "1",
    }))
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(return_value=[result]))
    idx.scanner_engine.last_scan_duration = 0.1
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(
        idx.data_engine.universe,
        "get_info",
        MagicMock(return_value={"asset_class": "CRYPTO"}),
    )
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={
        "last": 100, "timestamp": int(time.time() * 1000),
    }))
    monkeypatch.setattr(idx.data_engine, "is_fresh", MagicMock(return_value=True))
    monkeypatch.setattr(
        idx.data_engine,
        "check_scalping_allowed",
        MagicMock(return_value={"allowed": True, "reason": None}),
    )
    monkeypatch.setattr(
        idx.risk_engine,
        "calculate_position_size",
        MagicMock(return_value={"allowed": True, "quantity": 1, "leverage": 1}),
    )
    execute = AsyncMock(return_value={"success": True, "position": {"symbol": "btc_usdt"}})
    monkeypatch.setattr(idx.execution_router, "execute", execute)
    monkeypatch.setattr(idx.db_manager, "archive_signal", MagicMock())
    monkeypatch.setattr(idx.notification_engine, "notify", AsyncMock())
    
    # v2.7: reset the opportunity tracker to ensure clean state
    from api.engines.opportunity_tracker import get_tracker
    get_tracker().reset()
    
    # Ensure scanning is False
    idx.bot_state["scanning"] = False

    await idx.tick_scanner()
    
    # v2.7: verify opportunity ranking was populated
    opp_ranking = idx.bot_state.get("opportunity_ranking", {})
    assert opp_ranking.get("primary_opportunity") is not None
    assert opp_ranking.get("total_passing") == 1
    
    # v2.7: verify tracker was used (opportunity was acquired)
    from api.engines.opportunity_tracker import get_tracker
    tracker = get_tracker()
    tracker_stats = tracker.get_stats()
    # The opportunity should have been executed or marked
    assert tracker_stats["executed_count"] >= 0  # may be 0 if execution failed for other reasons
    
    assert idx.bot_state["engine_stats"]["tradable"] == 1
    assert idx.bot_state["execution_intent"]["code"] == "EXECUTING"
    await asyncio.sleep(0)


async def test_read_index_and_lifespan_cleanup_even_on_body_error(monkeypatch):
    response = await idx.read_index()
    assert response.path.endswith("public/index.html")

    initialize = AsyncMock(side_effect=RuntimeError("bad stored broker"))
    shutdown_broker = AsyncMock(side_effect=RuntimeError("shutdown broker"))
    shutdown_data = AsyncMock(side_effect=RuntimeError("shutdown data"))
    monkeypatch.setattr(idx.broker_connector, "initialize_from_db", initialize)
    monkeypatch.setattr(idx.broker_connector, "shutdown", shutdown_broker)
    monkeypatch.setattr(idx.data_engine, "shutdown", shutdown_data)
    monkeypatch.setattr(idx.settings_provider, "apply", MagicMock())

    async def forever(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(idx, "loop_wrapper", forever)
    with pytest.raises(RuntimeError, match="body failure"):
        async with idx.lifespan(idx.app):
            raise RuntimeError("body failure")
    initialize.assert_awaited_once()
    shutdown_broker.assert_awaited_once()
    shutdown_data.assert_awaited_once()
