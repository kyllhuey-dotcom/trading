"""v2.9 regression tests for automatic RSI exclusivity and safety gates."""

from unittest.mock import AsyncMock
import copy

import pytest

from api import index as idx
from api.engines.constants import DEFAULT_RSI_RISK_REWARD
from api.engines.radar import enrich_radar_row
from api.engines.settings_schema import SETTINGS_SPEC
from api.engines.signal_engine import SignalEngine


@pytest.fixture(autouse=True)
def restore_bot_state():
    original = copy.deepcopy(idx.bot_state)
    yield
    idx.bot_state.clear()
    idx.bot_state.update(original)


def test_active_strategy_is_always_rsi_for_automation():
    engine = SignalEngine()
    engine.set_active_strategies(["structure", "tape", "arbitrage"])
    assert engine.active_strategy_names == ["rsi"]
    assert "rsi" in engine.strategies
    assert "rsi" in engine.cost_filter_strategies


def test_rsi_default_rr_is_2_0():
    assert DEFAULT_RSI_RISK_REWARD == 2.0
    assert SETTINGS_SPEC["risk_reward_ratio"]["default"] == "2.0"
    engine = SignalEngine()
    assert engine.effective_risk_reward("btc_usdt", "rsi") == 2.0


def test_radar_disables_legacy_strategy_execution():
    row = enrich_radar_row({
        "symbol": "btc_usdt",
        "tradable": True,
        "signal_data": {"strategy": "structure"},
    })
    assert row["auto_execution_allowed"] is False
    assert row["tradable"] is False


@pytest.mark.asyncio
async def test_execute_signal_rejects_non_rsi(monkeypatch):
    idx.bot_state["latest_scan"] = [{
        "symbol": "btc_usdt",
        "score": 99,
        "signal_data": {
            "status": "SIGNAL_DETECTED", "strategy": "tape", "market_id": "btc_usdt",
            "entry": 100.0, "sl": 99.0, "tp": 102.0,
        },
    }]
    result = await idx._execute_signal_for_market("btc_usdt")
    assert result["success"] is False
    assert "RSI" in result["reason"]


@pytest.mark.asyncio
async def test_tick_scanner_does_not_execute_non_rsi(monkeypatch):
    candidate = {
        "symbol": "btc_usdt", "status": "LIVE", "score": 99, "tradable": True,
        "asset_class": "CRYPTO", "spread": 0.01, "volume": 1_000_000,
        "data_age_ms": 10, "realtime_source": True,
        "signal_data": {
            "status": "SIGNAL_DETECTED", "strategy": "structure", "market_id": "btc_usdt",
            "direction": "BUY", "entry": 100.0, "sl": 95.0, "tp": 110.0,
        },
        "diagnosis": {"checks": {
            "NEWS_CLEAR": "PASS", "SESSION_ALLOWED": "PASS", "DAY_ALLOWED": "PASS",
            "MARKET_OPEN": "PASS", "LIQUIDITY_VALID": "PASS",
        }},
    }
    monkeypatch.setattr(idx.scanner_engine, "scan_all", AsyncMock(return_value=[candidate]))
    monkeypatch.setattr(idx.manager, "broadcast", AsyncMock())
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(idx.execution_router, "execute", execute)
    monkeypatch.setattr(idx.settings_provider, "get", lambda: {
        "scan_interval_seconds": "30", "min_signal_score": "84",
        "max_new_positions_per_scan": "3", "fee_pct": "0.05",
        "sim_slippage_pct": "0.05", "max_spread_pct": "0.5",
    })
    idx.bot_state.update(is_running=True, armed=True, scanning=False,
                         active_trades=[], balance=10_000.0)
    await idx.tick_scanner(force=True)
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_market_snapshot_forces_rsi(monkeypatch):
    import pandas as pd
    frame = pd.DataFrame({
        "Timestamp": list(range(50)),
        "Open": [1] * 50, "High": [2] * 50, "Low": [0.5] * 50, "Close": [1.2] * 50,
        "Volume": [10] * 50,
    })
    monkeypatch.setattr(idx.data_engine, "fetch_ohlcv", AsyncMock(return_value=frame))
    monkeypatch.setattr(idx.data_engine, "fetch_ticker", AsyncMock(return_value={
        "last": 1.2, "status": "LIVE", "timestamp": 1, "spread": 0.01, "volume": 10,
    }))
    monkeypatch.setattr(idx.news_engine, "check_trading_allowed", AsyncMock(return_value={
        "trading_allowed": True, "news_ok": True, "session_ok": True, "day_ok": True,
        "blocking_event": None, "next_events": [],
    }))
    captured = {}

    def _capture(*args, **kwargs):
        captured["mode"] = kwargs.get("strategy_mode")
        return {"status": "NO_TRADE", "strategy": "rsi", "score": 0, "reason": "x",
                "market_id": "btc_usdt"}

    monkeypatch.setattr(idx.signal_engine, "generate_signal", _capture)
    idx._snapshot_cache.clear()
    await idx._build_snapshot("btc_usdt")
    assert captured["mode"] == "rsi"
