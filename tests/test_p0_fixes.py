"""
Regression tests for the critical P0 bugs fixed in v2.0:
 1. Signals must carry market_id (execution was impossible before)
 2. Execution must refuse signals without market_id (explicit failure)
 3. Execution must succeed when market_id is present
 4. CCXTAdapter must accept credentials as arguments
 5. All broker adapters must implement close_all_positions
 6. Mutating endpoints must be protected when ADMIN_API_KEY is set
 7. /api/status must always honor its contract
 8. Live settings reload must change engine behavior
"""
import pytest
import pandas as pd

from api.engines.db_manager import DatabaseManager
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
from api.engines.broker_adapters.primexbt_adapter import PrimeXBTAdapter
import api.index as idx


# --------------------------------------------------------------------------- #
# 1. market_id propagation
# --------------------------------------------------------------------------- #
def test_structure_signal_carries_market_id():
    signal = SignalEngine()
    analysis = {
        "status": "VALID", "trend": "BULLISH", "market_state": "TRENDING",
        "is_hh": True, "is_hl": True, "is_lh": False, "is_ll": False,
        "htf_bias": "BULLISH", "momentum": 0.5, "bos": True, "choch": False,
        "last_high": 110, "last_low": 90,
    }
    df = pd.DataFrame({
        'High': [105, 106, 107, 108, 109, 110] * 5,
        'Low': [95, 96, 97, 98, 99, 100] * 5,
        'Close': [100, 101, 102, 103, 104, 105] * 5,
        'Volume': [1000] * 30,
    })
    res = signal.generate_signal(analysis, {"trading_allowed": True}, df,
                                 strategy_mode="structure", market_id="btc_usdt")
    assert res["market_id"] == "btc_usdt"
    if res["status"] == "SIGNAL_DETECTED":
        assert res["entry"] and res["sl"] and res["tp"]


def test_arbitrage_signal_carries_market_id():
    signal = SignalEngine()
    df = pd.DataFrame({'High': [110] * 50, 'Low': [90] * 50, 'Close': [100] * 50,
                       'Open': [100] * 50, 'Volume': [1000] * 50})
    cross_quotes = [{"last": 100.0}, {"last": 100.5}]
    res = signal.generate_signal({"market_id": "btc_usdt", "status": "VALID"},
                                 {"trading_allowed": True}, df,
                                 strategy_mode="arbitrage", cross_quotes=cross_quotes)
    assert res["market_id"] == "btc_usdt"


# --------------------------------------------------------------------------- #
# 2. Execution requires (and accepts) market_id
# --------------------------------------------------------------------------- #
class MockUniverse:
    def get_market_status(self, s):
        return "OPEN"


@pytest.mark.asyncio
async def test_execution_rejects_missing_market_id(tmp_path):
    db = DatabaseManager(str(tmp_path / "t1.db"))
    eng = ExecutionEngine(portfolio=PortfolioEngine(db), db_manager=db,
                          risk_engine=RiskEngine(), universe=MockUniverse())
    sig = {"direction": "BUY", "entry": 100.0, "sl": 95.0, "tp": 110.0}
    risk = {"allowed": True, "quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1}
    res = await eng.execute_order("DEMO", sig, risk, {"last": 100.0})
    assert res["success"] is False
    assert res["reason"] == "MISSING_MARKET_ID"


@pytest.mark.asyncio
async def test_execution_succeeds_with_market_id(tmp_path):
    db = DatabaseManager(str(tmp_path / "t2.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_rejection_prob', '0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_latency_ms', '0')")
    eng = ExecutionEngine(portfolio=PortfolioEngine(db), db_manager=db,
                          risk_engine=RiskEngine(), universe=MockUniverse())
    sig = {"market_id": "btc_usdt", "display_symbol": "BTC/USDT", "direction": "BUY",
           "entry": 60000.0, "sl": 59000.0, "tp": 62000.0, "atr": 100.0, "strategy": "structure"}
    risk = {"allowed": True, "quantity": 0.001, "leverage": 0.5, "estimated_fees": 1.0}
    ticker = {"last": 60000.0, "bid": 59999.0, "ask": 60001.0}

    res = await eng.execute_order("DEMO", sig, risk, ticker)
    assert res["success"] is True
    pos = res["position"]
    assert pos["symbol"] == "btc_usdt"
    assert pos["display_symbol"] == "BTC/USDT"
    assert pos["id"].startswith("SIM-")

    res2 = await eng.execute_order("DEMO", {**sig, "market_id": "eth_usdt", "display_symbol": "ETH/USDT"},
                                   risk, ticker)
    assert res2["success"] is True
    assert res2["position"]["id"] != pos["id"]  # unique IDs


# --------------------------------------------------------------------------- #
# 3. Broker adapters
# --------------------------------------------------------------------------- #
def test_ccxt_adapter_accepts_credentials():
    a = CCXTAdapter("binance", "key123", "secret456", "pass789")
    assert a.api_key == "key123"
    assert a.api_secret == "secret456"
    assert a.passphrase == "pass789"
    assert a.exchange_id == "binance"


def test_all_adapters_implement_close_all_positions():
    for adapter_cls in (CCXTAdapter, PrimeXBTAdapter):
        assert hasattr(adapter_cls, "close_all_positions")
        assert hasattr(adapter_cls, "execute_order")
        assert hasattr(adapter_cls, "get_balance")


# --------------------------------------------------------------------------- #
# 4. Authentication
# --------------------------------------------------------------------------- #
def test_mutating_endpoints_protected(monkeypatch):
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "secret-key-123")
    from fastapi.testclient import TestClient
    client = TestClient(idx.app)

    for path in ["/api/start", "/api/stop", "/api/arm", "/api/mode",
                 "/api/emergency-stop", "/api/demo/reset"]:
        r = client.post(path)
        assert r.status_code == 401, f"{path} should require the API key"

    r = client.post("/api/start", headers={"X-API-Key": "secret-key-123"})
    assert r.status_code == 200
    # cleanup: stop the bot state we just started
    client.post("/api/stop", headers={"X-API-Key": "secret-key-123"})
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "")


# --------------------------------------------------------------------------- #
# 5. /api/status contract
# --------------------------------------------------------------------------- #
def test_status_contract():
    from fastapi.testclient import TestClient
    client = TestClient(idx.app)
    r = client.get("/api/status?market_id=btc_usdt")
    assert r.status_code == 200
    data = r.json()
    for key in ("status", "status_display", "diagnosis", "news", "analysis", "signal",
                "balance", "equity", "daily_pnl", "drawdown", "best_setups",
                "selected_market", "asset_info", "broker_info"):
        assert key in data, f"missing {key} in /api/status"
    assert data["diagnosis"] is not None
    assert "checks" in data["diagnosis"]


# --------------------------------------------------------------------------- #
# 6. Live settings reload
# --------------------------------------------------------------------------- #
def test_risk_settings_reload_changes_sizing():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    before = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    risk.apply_settings({"max_risk_pct": "2.0", "max_leverage": "20",
                         "max_daily_loss_pct": "3.0", "cool_down_mins": "30",
                         "max_open_positions": "3"})
    after = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert after["quantity"] == 2 * before["quantity"]
    assert after["risk_pct"] == 2.0
