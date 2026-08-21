import pytest
import pandas as pd
import asyncio
from api.engines.risk_engine import RiskEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.db_manager import DatabaseManager
from api.engines.portfolio_engine import PortfolioEngine

def test_correlation_risk():
    risk = RiskEngine(max_risk_pct=1.0)
    active = [
        {"symbol": "BTC_USDT", "direction": "BUY"},
        {"symbol": "ETH_USDT", "direction": "BUY"}
    ]
    
    # Check BTC again - should be blocked
    res = risk.check_correlation("BTC_USDT", active)
    assert res["allowed"] == False
    assert "Correlation" in res["reason"]
    
    # Check SOL - should be allowed (total 2 < 5)
    res = risk.check_correlation("SOL_USDT", active)
    assert res["allowed"] == True

@pytest.mark.asyncio
async def test_trailing_stop_logic():
    db = DatabaseManager("data/test_lot9.db")
    # Setup settings
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trailing_stop_active', 'true')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trailing_stop_distance_atr', '1.0')")
    
    portfolio = PortfolioEngine(db)
    risk = RiskEngine()
    
    # Mock Universe
    class MockUniverse:
        def get_market_status(self, s): return "OPEN"
    
    exec_engine = ExecutionEngine(portfolio, db, risk, MockUniverse())
    
    # Create a trade: Entry 100, SL 90, TP 120, ATR 5.
    trade = {
        "id": "T1", "mode": "DEMO", "symbol": "BTC_USDT", "display_symbol": "BTC/USDT",
        "direction": "BUY", "entry_price": 100.0, "quantity": 1.0, "sl": 90.0, "tp": 120.0,
        "leverage": 1.0, "fees": 0.0, "open_time": "now", "status": "OPEN", "pnl": 0.0,
        "metadata": {"atr": 5.0}
    }
    db.save_trade(trade)
    
    # Price moves to 110. ATR is 5. New SL should be 110 - 5 = 105.
    # 105 > 90, so SL should update.
    tickers = {"BTC/USDT": {"last": 110.0, "bid": 110.0, "ask": 110.0}}
    await exec_engine.update_active_positions("DEMO", tickers)
    
    updated = db.get_active_positions("DEMO")[0]
    assert updated["sl"] == 105.0

@pytest.mark.asyncio
async def test_partial_tp_and_breakeven():
    db = DatabaseManager("data/test_lot9_partial.db")
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('partial_tp_ratio', '1.0')")
    
    portfolio = PortfolioEngine(db)
    portfolio.set_balance("DEMO", 1000.0)
    risk = RiskEngine()
    
    class MockUniverse:
        def get_market_status(self, s): return "OPEN"
    
    exec_engine = ExecutionEngine(portfolio, db, risk, MockUniverse())
    
    # Entry 100, SL 90 (Risk=10). Partial TP at 1:1 RR = 110.
    trade = {
        "id": "T2", "mode": "DEMO", "symbol": "BTC_USDT", "display_symbol": "BTC/USDT",
        "direction": "BUY", "entry_price": 100.0, "quantity": 2.0, "sl": 90.0, "tp": 150.0,
        "leverage": 1.0, "fees": 0.0, "open_time": "now", "status": "OPEN", "pnl": 0.0,
        "metadata": {"atr": 5.0}
    }
    db.save_trade(trade)
    
    # Price hits 112 (> 110).
    tickers = {"BTC/USDT": {"last": 112.0, "bid": 112.0, "ask": 112.0}}
    await exec_engine.update_active_positions("DEMO", tickers)
    
    updated = db.get_active_positions("DEMO")[0]
    # Quantity should be halved (2.0 -> 1.0)
    assert updated["quantity"] == 1.0
    # SL should be at least at entry (Break-even or Trailing)
    assert updated["sl"] >= 100.0
    # Metadata should show partial TP hit
    assert updated["metadata"]["partial_tp_hit"] == True
    # Balance should have increased by (110-100)*1.0 = 10€
    assert portfolio.get_balance("DEMO") > 1000.0
