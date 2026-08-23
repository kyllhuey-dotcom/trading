"""Integration-light tests for remaining API handlers."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api import index as idx


async def test_close_position_api_paths(monkeypatch):
    idx.bot_state["mode"] = "DEMO"
    idx.bot_state["active_trades"] = []
    res = await idx.close_position_api("btc_usdt")
    assert res["success"] is False

    idx.bot_state["active_trades"] = [{"symbol": "btc_usdt"}]
    idx.bot_state["mode"] = "REAL"
    res = await idx.close_position_api("btc_usdt")
    assert "REAL" in res["reason"]

    idx.bot_state["mode"] = "DEMO"
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value=None))
    res = await idx.close_position_api("btc_usdt")
    assert "No market data" in res["reason"]

    monkeypatch.setattr(idx.data_engine, "fetch_ticker",
                        AsyncMock(return_value={"last": 101.0}))
    monkeypatch.setattr(idx.demo_execution, "close_position", MagicMock(return_value=None))
    res = await idx.close_position_api("btc_usdt")
    assert res["success"] is False

    monkeypatch.setattr(
        idx.demo_execution, "close_position",
        MagicMock(return_value={"pnl": 1.0, "net_pnl": 0.8}),
    )
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    res = await idx.close_position_api("btc_usdt")
    assert res["success"] is True
    assert res["exit_price"] == 101.0


async def test_get_optimization_and_history_error(monkeypatch):
    idx.bot_state["balance"] = 250.0
    monkeypatch.setattr(
        idx.market_tuning_engine, "markets_feasible_for_capital",
        MagicMock(return_value={"crypto": []}),
    )
    monkeypatch.setattr(
        idx.db_manager, "get_history",
        MagicMock(return_value=[
            {"symbol": "btc_usdt", "pnl": 10},
            {"symbol": "btc_usdt", "pnl": -2},
            {"market_id": "eth_usdt", "pnl": -8},
        ]),
    )
    out = await idx.get_optimization("DEMO")
    assert "best_markets" in out and "worst_markets" in out
    assert out["balance"] == 250.0

    monkeypatch.setattr(idx.db_manager, "get_history", MagicMock(side_effect=RuntimeError("db")))
    out = await idx.get_optimization("DEMO")
    assert out["best_markets"] == []


async def test_execute_signal_helper_and_api(monkeypatch):
    with pytest.raises(HTTPException):
        await idx.execute_signal_api({})

    monkeypatch.setattr(idx.data_engine.universe, "get_info", MagicMock(return_value=None))
    with pytest.raises(HTTPException):
        await idx._execute_signal_for_market("nope")

    info = {"display_symbol": "BTC/USDT", "asset_class": "CRYPTO"}
    monkeypatch.setattr(idx.data_engine.universe, "get_info", MagicMock(return_value=info))
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "min_signal_score": "bad",
        "allow_delayed_data_trading": "false",
        "fee_pct": "0.05",
        "sim_slippage_pct": "0.05",
    }))
    idx.bot_state["latest_scan"] = []
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={
        "signal": {"strategy": "structure", "score": 90, "status": "SIGNAL_DETECTED", "entry": 1},
    }))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert "Only RSI" in res["reason"]

    sig = {
        "strategy": "rsi", "score": 10, "status": "SIGNAL_DETECTED",
        "entry": 100, "sl": 95, "tp": 110, "tradable": True,
    }
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={"signal": sig}))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert "below min_signal_score" in res["reason"]

    sig = {**sig, "score": 90, "status": "NO_TRADE", "entry": None}
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={"signal": sig}))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert "SIGNAL_DETECTED" in res["reason"]

    sig = {
        "strategy": "rsi", "score": 90, "status": "SIGNAL_DETECTED",
        "entry": 100, "sl": 95, "tp": 110, "tradable": False, "main_reason": "X",
    }
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={"signal": sig}))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert res["reason"] == "X"

    sig = {**sig, "tradable": True}
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={"signal": sig}))
    monkeypatch.setattr(idx.data_engine, "check_scalping_allowed",
                        MagicMock(return_value={"allowed": False, "reason": "DELAYED"}))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert res["reason"] == "DELAYED"

    monkeypatch.setattr(idx.data_engine, "check_scalping_allowed",
                        MagicMock(return_value={"allowed": True, "reason": None}))
    idx.bot_state["active_trades"] = [{"symbol": "btc_usdt"}]
    res = await idx._execute_signal_for_market("btc_usdt")
    assert "already open" in res["reason"]

    idx.bot_state["active_trades"] = []
    idx.bot_state["balance"] = 10_000
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value=None))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert "No market data" in res["reason"]

    monkeypatch.setattr(idx.data_engine, "fetch_ticker",
                        AsyncMock(return_value={"last": 100, "spread": 0.01}))
    monkeypatch.setattr(idx.risk_engine, "calculate_position_size",
                        MagicMock(return_value={"allowed": False, "reason": "risk-x"}))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert res["reason"] == "risk-x"

    monkeypatch.setattr(idx.risk_engine, "calculate_position_size",
                        MagicMock(return_value={"allowed": True, "quantity": 1}))
    monkeypatch.setattr(idx.execution_router, "execute",
                        AsyncMock(return_value={"success": True}))
    res = await idx._execute_signal_for_market("btc_usdt")
    assert res["success"] is True


async def test_broker_capabilities(monkeypatch):
    monkeypatch.setattr(idx.db_manager, "get_broker_public_list", MagicMock(return_value=[
        {"broker_id": "main", "exchange_id": "binance", "is_active": True},
        {"broker_id": "ok", "exchange_id": "okx", "is_active": False},
    ]))
    adapter = MagicMock()
    adapter._connected = True
    adapter.sandbox = True
    idx.broker_connector._adapters = {"main": adapter}
    try:
        out = await idx.get_broker_capabilities()
    finally:
        delattr(idx.broker_connector, "_adapters")
    assert out["total_brokers"] == 2
    assert out["connected_brokers"] == 1
    by_id = {c["broker_id"]: c for c in out["capabilities"]}
    assert by_id["ok"]["passphrase_required"] is True
    assert by_id["main"]["runtime_status"] == "CONNECTED"


async def test_add_wallet_validation(monkeypatch):
    monkeypatch.setattr(idx.db_manager, "save_wallet", MagicMock())
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    monkeypatch.setattr(idx.broker_connector, "web3_wallets", {})
    with pytest.raises(HTTPException):
        await idx.add_wallet_api({"wallet_id": "w", "address": "not-evm", "chain_type": "ethereum"})
    with pytest.raises(HTTPException):
        await idx.add_wallet_api({"wallet_id": "w", "address": "short", "chain_type": "solana"})
    with pytest.raises(HTTPException):
        await idx.add_wallet_api({"wallet_id": "w", "address": "xx", "chain_type": "bitcoin"})
    ok = await idx.add_wallet_api({
        "wallet_id": "sol1",
        "address": "So11111111111111111111111111111111111111112",
        "chain_type": "solana",
    })
    assert ok["success"] is True


async def test_get_metrics_winrate_branches(monkeypatch):
    monkeypatch.setattr(idx.metrics_engine, "snapshot", MagicMock(return_value={
        "signals_generated_by_strategy": {},
        "signals_blocked_by_strategy": {},
        "orders_by_mode": {},
        "latency": {},
        "data_age": {},
        "total_errors": 0,
        "institutional": {},
    }))
    monkeypatch.setattr(
        idx.portfolio_engine, "get_performance_report",
        MagicMock(side_effect=[{"wr": 1}, RuntimeError("boom")]),
    )
    out = await idx.get_metrics()
    assert out["winrate_simulated"]["DEMO"] == {"wr": 1}
    assert out["winrate_simulated"]["REAL"] is None
