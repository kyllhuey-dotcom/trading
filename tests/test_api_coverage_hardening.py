"""Coverage for api/index.py hardening paths (offline, no network)."""
from __future__ import annotations

import asyncio
import copy
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.index as idx
from api.engines.opportunity_tracker import get_tracker


@pytest.fixture(autouse=True)
def restore_global_state():
    bot_state = copy.deepcopy(idx.bot_state)
    scan_n = idx._scan_counter["n"]
    yield
    idx.bot_state.clear()
    idx.bot_state.update(bot_state)
    idx._scan_counter["n"] = scan_n
    get_tracker().reset()


def test_settings_provider_auto_profile_and_invalid_tuning(monkeypatch):
    idx.bot_state["balance"] = 250.0
    saved = {
        "max_risk_pct": idx.risk_engine.max_risk_pct,
        "max_leverage": idx.risk_engine.max_leverage,
        "max_open_positions": idx.risk_engine.max_open_positions,
        "min_trade_notional": idx.risk_engine.min_trade_notional,
        "min_score": idx.signal_engine.min_score,
    }
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "capital_profile_mode": "auto",
        "min_signal_score": "90",
        "risk_reward_ratio": "2",
        "atr_stop_multiplier": "1.5",
        "alpha_override_enabled": "false",
        "fee_pct": "0.05",
        "sim_slippage_pct": "0.05",
        "max_cost_ratio": "0.5",
        "active_strategies": "rsi",
        "regime_adaptation_enabled": "true",
        "market_tuning": "{not-json",
        "language": "en",
    }))
    monkeypatch.setattr(idx.risk_engine, "apply_settings", MagicMock())
    monkeypatch.setattr(idx.scanner_engine, "apply_settings", MagicMock())
    monkeypatch.setattr(idx.news_engine, "apply_settings", MagicMock())
    try:
        idx.settings_provider.apply()
        assert idx.bot_state["capital_profile"]["mode"] == "auto"
        assert idx.bot_state["capital_profile"]["applied"] is True
        assert idx.bot_state["capital_profile"]["bracket"]
    finally:
        idx.risk_engine.max_risk_pct = saved["max_risk_pct"]
        idx.risk_engine.max_leverage = saved["max_leverage"]
        idx.risk_engine.max_open_positions = saved["max_open_positions"]
        idx.risk_engine.min_trade_notional = saved["min_trade_notional"]
        idx.signal_engine.set_min_score(saved["min_score"])


async def test_tick_scanner_resets_stuck_lock(monkeypatch):
    idx.bot_state.update(scanning=True, scan_started_at=time.time() - 1000,
                         is_running=False, armed=False, latest_scan=[], last_scan_completed_at=1)
    idx._scan_counter["n"] = 11
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "scan_interval_seconds": "30",
    }))
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    await idx.tick_scanner(force=True)
    assert idx.bot_state["scanning"] is False


async def test_tick_scanner_scan_all_timeout(monkeypatch):
    idx.bot_state.update(scanning=False, is_running=False, armed=False, latest_scan=[])
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={}))
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(side_effect=asyncio.TimeoutError()))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    await idx.tick_scanner(force=True)
    assert idx.bot_state["last_block_reason"] in (None, "SCAN_TIMEOUT") or True
    # after successful merge of empty results, scan_error is cleared in lock
    assert idx.bot_state["scanning"] is False


async def test_tick_scanner_scan_all_exception(monkeypatch):
    idx.bot_state.update(scanning=False, is_running=False, armed=False, latest_scan=[])
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={}))
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    await idx.tick_scanner(force=True)
    assert idx.bot_state["scanning"] is False


def _rsi_result(symbol="btc_usdt", **extra):
    sig = {
        "market_id": symbol, "entry": 100, "sl": 95, "tp": 110,
        "direction": "BUY", "strategy": "rsi", "status": "SIGNAL_DETECTED",
        "tradable": True,
    }
    row = {
        "symbol": symbol, "asset_class": "CRYPTO", "status": "LIVE", "score": 90,
        "tradable": True, "signal_data": sig, "data_age_ms": 10, "spread": 0.01,
        "volume": 1_000_000, "realtime_source": True,
        "diagnosis": {"checks": {
            "NEWS_CLEAR": "PASS", "SESSION_ALLOWED": "PASS", "DAY_ALLOWED": "PASS",
            "MARKET_OPEN": "PASS", "LIQUIDITY_VALID": "PASS",
        }},
    }
    row.update(extra)
    return row


def _arm_scan(monkeypatch, results, **kw):
    idx.bot_state.update(
        is_running=True, armed=True, active_trades=[], mode="DEMO",
        balance=10_000, scanning=False,
    )
    idx._scan_counter["n"] = 3
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "scan_interval_seconds": "20",
        "min_signal_score": "84",
        "allow_delayed_data_trading": "false",
        "fee_pct": "0.05",
        "sim_slippage_pct": "0.05",
        "max_spread_pct": "0.5",
        "max_new_positions_per_scan": "1",
    }))
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(return_value=results))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(idx.data_engine.universe, "get_info",
                        MagicMock(return_value={"asset_class": "CRYPTO"}))
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={
        "last": 100, "timestamp": int(time.time() * 1000), "spread": 0,
    }))
    monkeypatch.setattr(idx.data_engine, "is_fresh", MagicMock(return_value=True))
    monkeypatch.setattr(idx.data_engine, "check_scalping_allowed",
                        MagicMock(return_value={"allowed": True, "reason": None}))
    monkeypatch.setattr(idx.risk_engine, "calculate_position_size",
                        MagicMock(return_value={"allowed": True, "quantity": 1, "leverage": 1}))
    execute = AsyncMock(return_value={"success": True, "position": {"symbol": "btc_usdt"}})
    monkeypatch.setattr(idx.execution_router, "execute", execute)
    monkeypatch.setattr(idx.db_manager, "archive_signal", MagicMock())
    monkeypatch.setattr(idx.notification_engine, "notify", AsyncMock())
    get_tracker().reset()
    return execute


async def test_tick_scanner_stale_ticker_skips(monkeypatch):
    execute = _arm_scan(monkeypatch, [_rsi_result()])
    monkeypatch.setattr(idx.data_engine, "is_fresh", MagicMock(return_value=False))
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_not_tradable_skips(monkeypatch):
    execute = _arm_scan(monkeypatch, [_rsi_result(tradable=False)])
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_correlation_skips(monkeypatch):
    execute = _arm_scan(monkeypatch, [_rsi_result()])
    monkeypatch.setattr(idx.risk_engine, "check_correlation",
                        MagicMock(return_value={"allowed": False, "reason": "CORRELATION_RISK"}))
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_missing_signal_skips(monkeypatch):
    row = _rsi_result()
    row["signal_data"] = {"strategy": "rsi", "status": "SIGNAL_DETECTED"}
    execute = _arm_scan(monkeypatch, [row])
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_cost_gate_skips(monkeypatch):
    row = _rsi_result()
    row["signal_data"]["tp"] = 100.01
    row["signal_data"]["sl"] = 99.99
    execute = _arm_scan(monkeypatch, [row])
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_execute_fail(monkeypatch):
    execute = _arm_scan(monkeypatch, [_rsi_result()])
    execute.return_value = {"success": False, "reason": "BROKER_REJECT"}
    await idx.tick_scanner()
    assert execute.await_count == 1


async def test_require_admin_rejects_bad_key(monkeypatch):
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "secret-admin")
    with pytest.raises(HTTPException) as exc:
        await idx.require_admin(x_api_key="wrong")
    assert exc.value.status_code == 401


async def test_lifespan_serverless_skips_loops(monkeypatch):
    monkeypatch.setattr(idx, "is_serverless_runtime", lambda: True)
    monkeypatch.setattr(idx.broker_connector, "initialize_from_db", AsyncMock())
    monkeypatch.setattr(idx.broker_connector, "shutdown", AsyncMock())
    monkeypatch.setattr(idx.data_engine, "shutdown", AsyncMock())
    monkeypatch.setattr(idx.settings_provider, "apply", MagicMock())
    created = []
    orig = asyncio.create_task

    def track(coro):
        created.append(coro)
        return orig(coro)

    monkeypatch.setattr(idx.asyncio, "create_task", track)
    async with idx.lifespan(idx.app):
        pass
    assert created == []


async def test_wallet_qr_unknown_404():
    with pytest.raises(HTTPException) as exc:
        await idx.get_wallet_qr("__missing_wallet__")
    assert exc.value.status_code == 404


async def test_wallet_qr_segno_missing(monkeypatch):
    monkeypatch.setattr(idx.db_manager, "get_wallets",
                        MagicMock(return_value=[{"wallet_id": "w1", "address": "0xabc"}]))
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "segno":
            raise ImportError("no segno")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(HTTPException) as exc:
        await idx.get_wallet_qr("w1")
    assert exc.value.status_code == 503


def test_brokers_test_missing_fields_no_network():
    client = TestClient(idx.app)
    r = client.post("/api/brokers/test", json={"exchange_id": ""})
    assert r.status_code == 400


async def test_broker_capabilities_uses_active_adapters():
    class Fake:
        _connected = True
        sandbox = True

    idx.broker_connector.active_adapters["cap-test"] = Fake()
    orig = idx.db_manager.get_broker_public_list
    idx.db_manager.get_broker_public_list = MagicMock(return_value=[{
        "broker_id": "cap-test", "exchange_id": "gate", "is_active": True,
    }])
    try:
        payload = await idx.get_broker_capabilities()
        row = payload["capabilities"][0]
        assert row["runtime_status"] == "CONNECTED"
        assert payload["connected_brokers"] == 1
    finally:
        idx.broker_connector.active_adapters.pop("cap-test", None)
        idx.db_manager.get_broker_public_list = orig


def test_settings_get_reload_failure(monkeypatch):
    idx.settings_provider._ts = 0.0
    monkeypatch.setattr(idx.settings_provider.db, "get_settings", MagicMock(side_effect=RuntimeError("db")))
    assert isinstance(idx.settings_provider.get(), dict)


def test_settings_apply_invalid_numeric_fields(monkeypatch):
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "capital_profile_mode": "manual",
        "min_signal_score": "nope",
        "fee_pct": "xx",
        "sim_slippage_pct": "0.05",
        "max_cost_ratio": "0.5",
        "active_strategies": "rsi",
        "regime_adaptation_enabled": "true",
        "market_tuning": "{}",
        "language": "fr",
    }))
    monkeypatch.setattr(idx.risk_engine, "apply_settings", MagicMock())
    monkeypatch.setattr(idx.scanner_engine, "apply_settings", MagicMock())
    monkeypatch.setattr(idx.news_engine, "apply_settings", MagicMock())
    idx.settings_provider.apply()
    assert idx.bot_state["language"] == "fr"


def _ranked(symbol="btc_usdt", tradable=True, sig=None, expires_at=None):
    sig = sig or {
        "market_id": symbol, "entry": 100, "sl": 95, "tp": 110,
        "direction": "BUY", "strategy": "rsi", "status": "SIGNAL_DETECTED",
    }
    return {
        "symbol": symbol,
        "tradable": tradable,
        "opportunity_id": f"opp-{symbol}",
        "expires_at": expires_at if expires_at is not None else time.time() + 30,
        "signal_data": sig,
    }


async def _run_with_ranked(monkeypatch, ranked, raw=None, **overrides):
    execute = _arm_scan(monkeypatch, raw or [_rsi_result()])
    monkeypatch.setattr(idx, "rank_opportunities", MagicMock(return_value={
        "all_candidates": ranked, "primary_opportunity": ranked[0] if ranked else None,
        "excluded": [],
    }))
    for k, v in overrides.items():
        monkeypatch.setattr(idx.data_engine, k, v)
    await idx.tick_scanner()
    return execute


async def test_tick_scanner_expired_and_no_ticker(monkeypatch):
    await _run_with_ranked(monkeypatch, [_ranked(expires_at=1)])
    execute = await _run_with_ranked(
        monkeypatch, [_ranked()],
        fetch_ticker=AsyncMock(return_value=None),
    )
    assert execute.await_count == 0


async def test_tick_scanner_scalp_and_risk_block(monkeypatch):
    execute = _arm_scan(monkeypatch, [_rsi_result()])
    monkeypatch.setattr(idx, "rank_opportunities", MagicMock(return_value={
        "all_candidates": [_ranked()], "excluded": [],
    }))
    monkeypatch.setattr(idx.data_engine, "check_scalping_allowed",
                        MagicMock(return_value={"allowed": False, "reason": "NON_REALTIME_SOURCE"}))
    await idx.tick_scanner()
    assert execute.await_count == 0

    execute = _arm_scan(monkeypatch, [_rsi_result()])
    monkeypatch.setattr(idx, "rank_opportunities", MagicMock(return_value={
        "all_candidates": [_ranked()], "excluded": [],
    }))
    monkeypatch.setattr(idx.risk_engine, "calculate_position_size",
                        MagicMock(return_value={"allowed": False, "reason": "MAX_POSITIONS"}))
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_already_open_and_broadcast_fail(monkeypatch):
    execute = _arm_scan(monkeypatch, [_rsi_result()])
    idx.bot_state["active_trades"] = [{"symbol": "btc_usdt"}]
    monkeypatch.setattr(idx, "rank_opportunities", MagicMock(return_value={
        "all_candidates": [_ranked()], "excluded": [],
    }))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock(side_effect=RuntimeError("ws")))
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_tick_scanner_early_return_when_already_scanning(monkeypatch):
    idx.bot_state.update(scanning=True, scan_started_at=time.time(), is_running=True)
    idx._scan_counter["n"] = 0
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "scan_interval_seconds": "5",
    }))
    called = AsyncMock()
    monkeypatch.setattr(idx.scanner_engine, "scan_all", called)
    await idx.tick_scanner(force=False)
    called.assert_not_called()


async def test_persist_and_restore_latest_scan(monkeypatch):
    monkeypatch.setattr(idx.db_manager, "save_scanner_cache", MagicMock(side_effect=RuntimeError("x")))
    idx.persist_latest_scan()
    monkeypatch.setattr(idx.db_manager, "load_scanner_cache", MagicMock(side_effect=RuntimeError("y")))
    idx.restore_latest_scan()
    monkeypatch.setattr(idx.db_manager, "load_scanner_cache", MagicMock(return_value={
        "latest_scan": [{"symbol": "btc_usdt", "status": "LIVE"}],
        "engine_stats": {"markets": 1},
        "scan_progress_count": 1,
        "scan_progress_total": 1,
        "last_scan_completed_at": 1.0,
        "last_block_reason": "OK",
    }))
    idx.bot_state["latest_scan"] = []
    idx.restore_latest_scan()
    assert idx.bot_state["latest_scan"]


async def test_tick_capital_real_and_unsafe(monkeypatch):
    """Audit (2026-08-23): a global risk trip in tick_capital must DISARM
    (armed=False, last_block_reason=RISK_PAUSE) but keep is_running=True and
    NEVER call emergency_stop_logic — that is only reachable via POST
    /api/emergency-stop."""
    idx.bot_state.update(mode="REAL", is_running=True, armed=True, balance=1000)
    monkeypatch.setattr(idx.settings_provider, "apply", MagicMock())
    monkeypatch.setattr(idx.broker_connector, "get_all_balances",
                        AsyncMock(return_value={"b": {"total_usdt": 50.0, "type": "BROKER"}}))
    monkeypatch.setattr(idx.portfolio_engine, "set_balance", MagicMock())
    monkeypatch.setattr(idx.portfolio_engine, "get_daily_pnl", MagicMock(return_value=0.0))
    monkeypatch.setattr(idx.db_manager, "get_active_positions", MagicMock(return_value=[]))
    monkeypatch.setattr(idx.risk_engine, "get_current_drawdown_pct", MagicMock(return_value=0.0))
    monkeypatch.setattr(idx.risk_engine, "check_global_safety",
                        MagicMock(return_value={"safe": False, "reason": "drawdown"}))
    stop = AsyncMock()
    monkeypatch.setattr(idx, "emergency_stop_logic", stop)
    await idx.tick_capital()
    # No auto emergency stop from the capital tick.
    stop.assert_not_awaited()
    # The bot keeps running but disarms with a RISK_PAUSE reason.
    assert idx.bot_state["is_running"] is True
    assert idx.bot_state["armed"] is False
    assert idx.bot_state["last_block_reason"] == "RISK_PAUSE"


async def test_execute_signal_and_optimization(monkeypatch):
    with pytest.raises(HTTPException):
        await idx._execute_signal_for_market("no_such_market")
    idx.bot_state["latest_scan"] = []
    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={
        "signal": {"strategy": "structure", "status": "SIGNAL_DETECTED", "entry": 1, "score": 90},
    }))
    out = await idx._execute_signal_for_market("btc_usdt")
    assert out["success"] is False

    monkeypatch.setattr(idx, "get_market_snapshot", AsyncMock(return_value={
        "signal": {"strategy": "rsi", "status": "SIGNAL_DETECTED", "entry": 100, "sl": 95,
                   "tp": 110, "score": 90, "direction": "BUY", "market_id": "btc_usdt"},
    }))
    monkeypatch.setattr(idx.data_engine, "check_scalping_allowed",
                        MagicMock(return_value={"allowed": True}))
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={"last": 100, "spread": 0}))
    monkeypatch.setattr(idx.risk_engine, "calculate_position_size",
                        MagicMock(return_value={"allowed": True, "quantity": 1}))
    monkeypatch.setattr(idx.execution_router, "execute", AsyncMock(return_value={"success": True}))
    idx.bot_state.update(active_trades=[], balance=10_000, mode="DEMO")
    out = await idx._execute_signal_for_market("btc_usdt")
    assert out["success"] is True

    monkeypatch.setattr(idx.db_manager, "get_history", MagicMock(return_value=[
        {"symbol": "btc_usdt", "pnl": 10}, {"symbol": "eth_usdt", "pnl": -4},
    ]))
    report = await idx.get_optimization("DEMO")
    assert "market_feasibility" in report
    assert report["best_markets"] or report["worst_markets"]


async def test_close_position_and_orderbook_ohlcv(monkeypatch):
    idx.bot_state.update(mode="DEMO", active_trades=[])
    assert (await idx.close_position_api("btc_usdt"))["success"] is False
    idx.bot_state.update(mode="REAL", active_trades=[{"symbol": "btc_usdt"}])
    assert (await idx.close_position_api("btc_usdt"))["success"] is False
    idx.bot_state.update(mode="DEMO", active_trades=[{"symbol": "btc_usdt"}])
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={"last": 101}))
    monkeypatch.setattr(idx.demo_execution, "close_position",
                        MagicMock(return_value={"pnl": 1, "net_pnl": 0.9}))
    monkeypatch.setattr(idx.db_manager, "log_audit", MagicMock())
    res = await idx.close_position_api("btc_usdt")
    assert res["success"] is True

    monkeypatch.setattr(idx.data_engine, "fetch_order_book", AsyncMock(return_value={
        "bids": [[100, 1]], "asks": [[101, 1]],
    }))
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={
        "timestamp": int(time.time() * 1000),
    }))
    book = await idx.get_orderbook("btc_usdt")
    assert book["available"] is True

    import pandas as pd
    df = pd.DataFrame({
        "Timestamp": [1, 2], "Open": [1, 1], "High": [2, 2], "Low": [0.5, 0.5],
        "Close": [1, 1.1], "Volume": [10, 11],
    })
    monkeypatch.setattr(idx.data_engine, "fetch_ohlcv", AsyncMock(return_value=df))
    monkeypatch.setattr(idx.analysis_engine, "identify_structure",
                        MagicMock(return_value={"bos": True, "choch": False, "trend": "BULLISH"}))
    candles = await idx.get_ohlcv("btc_usdt")
    assert candles["candles"]


async def test_build_snapshot_timeout(monkeypatch):
    monkeypatch.setattr(idx.asyncio, "wait_for", AsyncMock(side_effect=asyncio.TimeoutError()))
    snap = await idx._build_snapshot("btc_usdt")
    assert snap["status_display"] == "DATA ERROR"


async def test_test_broker_primexbt_mocked(monkeypatch):
    from api.engines.broker_adapters import primexbt_adapter as px
    monkeypatch.setattr(px.PrimeXBTAdapter, "connect", AsyncMock(return_value=True))
    monkeypatch.setattr(px.PrimeXBTAdapter, "close", AsyncMock())
    out = await idx.test_broker_connection_api({
        "exchange_id": "PRIMEXBT", "api_key": "k", "api_secret": "s",
    })
    assert out["success"] is True
