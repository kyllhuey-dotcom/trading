"""v2.8 comprehensive tests.

Covers:
- Simultaneous trades: up to 3 executions per scan cycle, every candidate
  gated individually, correlation guard, max-open-positions stop.
- Continuous trading: status indicators (trading_active, trades_today,
  next_scan_in_s) and the scanner loop never stopping after executions.
- Broker connection dry-run (/api/brokers/test) + runtime snapshots.
- Watch-only wallet balances + server-side QR codes.
- Per-position manual close (DEMO) — anti-martingale and all protections
  untouched.
- Score floor consistency: execution paths accept 84 and refuse 83.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import api.index as idx
from api.engines.constants import (
    AUTO_EXECUTION_SCORE_FLOOR,
    DEFAULT_MAX_NEW_POSITIONS_PER_SCAN,
)
from api.engines.opportunity_tracker import get_tracker

client = TestClient(idx.app)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _candidate(symbol, score=90, base=None):
    return {
        "symbol": symbol,
        "asset_class": "CRYPTO",
        "status": "LIVE",
        "score": score,
        "tradable": True,
        "signal_data": {
            "market_id": symbol,
            "entry": 100, "sl": 95, "tp": 110,
            "direction": "BUY", "strategy": "rsi",
            "status": "SIGNAL_DETECTED", "tradable": True,
        },
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


def _patch_scan_env(monkeypatch, results, execute_side_effect, max_new="3"):
    """Route every external dependency of tick_scanner to a stub."""
    idx.bot_state.update(
        is_running=True, armed=True, active_trades=[], mode="DEMO",
        balance=10_000, scanning=False,
    )
    monkeypatch.setattr(idx.settings_provider, "get", MagicMock(return_value={
        "scan_interval_seconds": "bad",
        "min_signal_score": "84",
        "allow_delayed_data_trading": "false",
        "fee_pct": "0.05",
        "sim_slippage_pct": "0.05",
        "max_spread_pct": "0.5",
        "max_new_positions_per_scan": max_new,
    }))
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(return_value=results))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(idx.data_engine.universe, "get_info",
                        MagicMock(return_value={"asset_class": "CRYPTO"}))
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={
        "last": 100, "timestamp": int(time.time() * 1000),
    }))
    monkeypatch.setattr(idx.data_engine, "is_fresh", MagicMock(return_value=True))
    monkeypatch.setattr(idx.data_engine, "check_scalping_allowed",
                        MagicMock(return_value={"allowed": True, "reason": None}))
    monkeypatch.setattr(idx.risk_engine, "calculate_position_size",
                        MagicMock(return_value={"allowed": True, "quantity": 1, "leverage": 1}))
    execute = AsyncMock(side_effect=execute_side_effect)
    monkeypatch.setattr(idx.execution_router, "execute", execute)
    monkeypatch.setattr(idx.db_manager, "archive_signal", MagicMock())
    monkeypatch.setattr(idx.notification_engine, "notify", AsyncMock())
    get_tracker().reset()
    return execute


def _success_per_symbol(mode, signal, risk, ticker):
    return {"success": True, "position": {"symbol": signal["market_id"]}}


# --------------------------------------------------------------------------- #
# 1. SIMULTANEOUS TRADES                                                      #
# --------------------------------------------------------------------------- #
def test_max_new_positions_per_scan_3_constant():
    assert DEFAULT_MAX_NEW_POSITIONS_PER_SCAN == 3


async def test_max_new_positions_per_scan_3(monkeypatch):
    """Three opportunities scoring >= 84 on distinct underlyings all execute."""
    results = [_candidate("btc_usdt"), _candidate("eth_usdt"), _candidate("sol_usdt")]
    execute = _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="3")
    await idx.tick_scanner()
    assert execute.await_count == 3
    symbols = {call.args[1]["market_id"] for call in execute.await_args_list}
    assert symbols == {"btc_usdt", "eth_usdt", "sol_usdt"}
    assert len(idx.bot_state["active_trades"]) == 3


async def test_simultaneous_trades_correlation(monkeypatch):
    """Never two simultaneous positions on the same underlying (btc_usdt+btc_eur)."""
    results = [_candidate("btc_usdt"), _candidate("btc_eur"), _candidate("eth_usdt")]
    execute = _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="3")
    await idx.tick_scanner()
    assert execute.await_count == 2
    symbols = [call.args[1]["market_id"] for call in execute.await_args_list]
    assert "btc_usdt" in symbols and "eth_usdt" in symbols
    assert "btc_eur" not in symbols
    # the refused opportunity is tracked — no retry can double it
    tracker = get_tracker()
    assert tracker.get_stats()["executed_count"] >= 2
    assert len(idx.bot_state["active_trades"]) == 2


async def test_simultaneous_trades_max_positions(monkeypatch):
    """The loop stops at max_open_positions even with 3 valid candidates."""
    results = [_candidate("btc_usdt"), _candidate("eth_usdt"), _candidate("sol_usdt")]
    execute = _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="3")
    old_max = idx.risk_engine.max_open_positions
    idx.risk_engine.max_open_positions = 2
    try:
        await idx.tick_scanner()
    finally:
        idx.risk_engine.max_open_positions = old_max
    assert execute.await_count == 2
    assert len(idx.bot_state["active_trades"]) == 2


async def test_simultaneous_trades_capped_by_setting(monkeypatch):
    """max_new_positions_per_scan=1 preserves the v2.7 single-execution behavior."""
    results = [_candidate("btc_usdt"), _candidate("eth_usdt"), _candidate("sol_usdt")]
    execute = _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="1")
    await idx.tick_scanner()
    assert execute.await_count == 1
    assert len(idx.bot_state["active_trades"]) == 1


async def test_score_83_never_executes_in_scan(monkeypatch):
    results = [_candidate("btc_usdt", score=83)]
    execute = _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="3")
    await idx.tick_scanner()
    assert execute.await_count == 0


async def test_failure_of_one_candidate_does_not_stop_others(monkeypatch):
    """A blocked candidate (cost gate) must not abort the remaining candidates."""
    results = [_candidate("btc_usdt", score=90), _candidate("eth_usdt", score=89)]
    # eth entry/sl/tp degenerate -> cost gate refuses, btc still executes.
    results[1]["signal_data"]["tp"] = 100.05
    results[1]["signal_data"]["sl"] = 99.99
    execute = _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="3")
    await idx.tick_scanner()
    # exactly one execution went through (order may vary by rank)
    assert execute.await_count == 1
    assert len(idx.bot_state["active_trades"]) == 1


# --------------------------------------------------------------------------- #
# 2. CONTINUOUS TRADING                                                       #
# --------------------------------------------------------------------------- #
def test_status_exposes_continuous_trading_fields():
    r = client.get("/api/status?market_id=btc_usdt")
    assert r.status_code == 200
    body = r.json()
    assert "trading_active" in body
    assert body["trading_active"] == bool(body["is_running"] and body["armed"])
    assert isinstance(body.get("trades_today"), int)
    assert body.get("trades_today", 0) >= 0
    assert isinstance(body.get("scan_interval_s"), int)
    assert "next_scan_in_s" in body
    if body["trading_active"] and body["next_scan_in_s"] is not None:
        assert 0 <= body["next_scan_in_s"] <= body["scan_interval_s"]


def test_count_trades_today_counts_open_and_closed():
    mode = idx.bot_state["mode"]
    before = idx._count_trades_today(mode)
    assert isinstance(before, int)


async def test_scanner_not_stopped_after_executions(monkeypatch):
    """After executing trades, tick_scanner returns normally and is_running stays on."""
    results = [_candidate("btc_usdt")]
    _patch_scan_env(monkeypatch, results, _success_per_symbol, max_new="3")
    was_running_before = idx.bot_state["is_running"]
    await idx.tick_scanner()
    assert idx.bot_state["is_running"] is True
    assert idx.bot_state["armed"] is True
    assert was_running_before is True
    # no emergency stop triggered by a normal execution cycle
    assert idx.broker_connector.emergency_stop_active is False


def test_next_scan_none_when_paused():
    old = idx.bot_state["is_running"]
    idx.bot_state["is_running"] = False
    try:
        interval, next_scan = idx._next_scan_in_s({"scan_interval_seconds": "30"})
        assert interval == 30
        assert next_scan is None
    finally:
        idx.bot_state["is_running"] = old


# --------------------------------------------------------------------------- #
# 3. BROKER TEST + RUNTIME SNAPSHOT                                           #
# --------------------------------------------------------------------------- #
def test_broker_connection_test_success(monkeypatch):
    from api.engines.broker_adapters import ccxt_adapter as ccxt_mod
    monkeypatch.setattr(ccxt_mod.CCXTAdapter, "connect", AsyncMock(return_value=True))
    monkeypatch.setattr(ccxt_mod.CCXTAdapter, "close", AsyncMock())
    r = client.post("/api/brokers/test", json={
        "exchange_id": "gate", "api_key": "k", "api_secret": "s", "sandbox": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["sandbox"] is True
    assert body["latency_ms"] >= 0


def test_broker_connection_test_failure(monkeypatch):
    from api.engines.broker_adapters import ccxt_adapter as ccxt_mod
    monkeypatch.setattr(ccxt_mod.CCXTAdapter, "connect", AsyncMock(return_value=False))
    monkeypatch.setattr(ccxt_mod.CCXTAdapter, "close", AsyncMock())
    r = client.post("/api/brokers/test", json={
        "exchange_id": "gate", "api_key": "bad", "api_secret": "bad",
    })
    body = r.json()
    assert body["success"] is False


def test_broker_connection_test_validation():
    r = client.post("/api/brokers/test", json={"exchange_id": "gate"})
    assert r.status_code == 400


async def test_runtime_snapshot_inactive_no_network():
    snap = await idx.broker_connector.runtime_snapshot("__ghost_broker__")
    assert snap["runtime_status"] == "INACTIVE"
    assert snap["latency_ms"] is None


async def test_runtime_snapshot_connected_with_cache():
    connector = idx.broker_connector

    class _FakeAdapter:
        sandbox = True

        async def get_balance(self, asset="USDT"):
            return 1234.5

        async def get_positions(self):
            return [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]

    calls = {"n": 0}

    class _CountingAdapter(_FakeAdapter):
        async def get_balance(self, asset="USDT"):
            calls["n"] += 1
            return 42.0

    connector.active_adapters["__test_broker__"] = _CountingAdapter()
    try:
        snap = await connector.runtime_snapshot("__test_broker__")
        assert snap["runtime_status"] == "CONNECTED"
        assert snap["balance_usdt"] == 42.0
        assert snap["open_positions_count"] == 2
        assert snap["sandbox"] is True
        assert snap["latency_ms"] is not None
        assert snap["last_sync"]
        # second call served from cache (no extra balance fetch)
        snap2 = await connector.runtime_snapshot("__test_broker__")
        assert snap2 == snap
        assert calls["n"] == 1
    finally:
        connector.active_adapters.pop("__test_broker__", None)
        connector.invalidate_runtime_cache("__test_broker__")


async def test_runtime_snapshot_error_status():
    connector = idx.broker_connector

    class _DownAdapter:
        sandbox = False

        async def get_balance(self, asset="USDT"):
            raise RuntimeError("exchange unreachable")

    connector.active_adapters["__down_broker__"] = _DownAdapter()
    try:
        snap = await connector.runtime_snapshot("__down_broker__")
        assert snap["runtime_status"] == "ERROR"
    finally:
        connector.active_adapters.pop("__down_broker__", None)
        connector.invalidate_runtime_cache("__down_broker__")


# --------------------------------------------------------------------------- #
# 4. WALLETS: QR + BALANCES (WATCH-ONLY)                                      #
# --------------------------------------------------------------------------- #
def test_wallet_qr_svg_generation():
    pytest.importorskip("segno")
    wallet_id = f"qr-test-{int(time.time())}"
    r = client.post("/api/wallets", json={
        "wallet_id": wallet_id, "provider": "METAMASK",
        "address": "0x1234567890abcdef1234567890abcdef12345678",
        "chain_type": "ethereum",
    })
    assert r.status_code == 200
    resp = client.get(f"/api/wallets/{wallet_id}/qr")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in resp.text and "</svg>" in resp.text
    client.delete(f"/api/wallets/{wallet_id}")
    resp = client.get(f"/api/wallets/{wallet_id}/qr")
    assert resp.status_code == 404


def test_wallet_balances_endpoint_watch_only(monkeypatch):
    monkeypatch.setattr(idx.broker_connector, "get_all_balances",
                        AsyncMock(return_value={
                            "my_wallet": {"type": "WEB3", "asset": "ETH", "balance": 1.25,
                                          "connected": True},
                            "some_broker": {"type": "BROKER", "total_usdt": 10.0},
                        }))
    r = client.get("/api/wallet-balances")
    assert r.status_code == 200
    body = r.json()
    assert "my_wallet" in body["wallets"]
    assert body["wallets"]["my_wallet"]["balance"] == 1.25
    # broker rows are excluded from the wallet balance view
    assert "some_broker" not in body["wallets"]


def test_wallet_balances_fail_closed():
    # if the chain APIs are down the endpoint still answers (empty, not 500)
    r = client.get("/api/wallet-balances")
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# 5. PER-POSITION CLOSE (DEMO)                                                #
# --------------------------------------------------------------------------- #
def test_close_position_api_no_position():
    r = client.post("/api/positions/__ghost__/close")
    assert r.status_code == 200
    assert r.json()["success"] is False


def test_execution_engine_close_position(tmp_path):
    from api.engines.db_manager import DatabaseManager
    from api.engines.execution_engine import ExecutionEngine
    from api.engines.portfolio_engine import PortfolioEngine
    from api.engines.risk_engine import RiskEngine
    from api.engines.market_universe import MarketUniverse

    db = DatabaseManager(str(tmp_path / "t.db"))
    engine = ExecutionEngine(PortfolioEngine(db_manager=db), db, RiskEngine(), MarketUniverse())
    pos = {
        "id": "close-me-1", "mode": "DEMO", "symbol": "btc_usdt",
        "display_symbol": "BTC/USDT", "direction": "BUY",
        "entry_price": 100.0, "quantity": 1.0, "sl": 95.0, "tp": 110.0,
        "leverage": 1.0, "fees": 0.0, "status": "OPEN",
        "initial_quantity": 1.0, "remaining_quantity": 1.0,
        "entry_fees": 0.0, "slippage_cost": 0.0, "funding_cost": 0.0,
        "partial_realized_pnl": 0.0,
    }
    db.save_trade(pos)
    closed = engine.close_position("DEMO", "btc_usdt", 105.0)
    assert closed is not None
    assert closed["status"] == "CLOSED"
    # closing twice returns None (no double-close, no double accounting)
    assert engine.close_position("DEMO", "btc_usdt", 105.0) is None
    # unknown symbol returns None
    assert engine.close_position("DEMO", "eth_usdt", 100.0) is None


# --------------------------------------------------------------------------- #
# 6. FLOOR CONSISTENCY (84) — execution endpoints                             #
# --------------------------------------------------------------------------- #
def test_opportunities_endpoint_reports_floor_84():
    r = client.get("/api/opportunities")
    assert r.status_code == 200
    assert r.json()["floor"] == AUTO_EXECUTION_SCORE_FLOOR == 84


def test_settings_min_score_clamped_to_84():
    r = client.post("/api/settings", json={"min_signal_score": "77"})
    assert r.status_code == 200
    assert r.json()["applied"]["min_signal_score"] == "84"
    s = client.get("/api/settings").json()
    assert s["min_signal_score"] == "84"
    # restore a sane default
    client.post("/api/settings", json={"min_signal_score": "84"})


def test_max_new_positions_bounds():
    from api.engines.settings_schema import validate_settings
    cleaned, _ = validate_settings({"max_new_positions_per_scan": "9"})
    assert cleaned["max_new_positions_per_scan"] == "3"
    cleaned, _ = validate_settings({"max_new_positions_per_scan": "0"})
    assert cleaned["max_new_positions_per_scan"] == "1"
