import pytest
import time
import asyncio
from api.engines.execution_engine import ExecutionEngine
from api.engines.db_manager import DatabaseManager
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.risk_engine import RiskEngine

@pytest.mark.asyncio
async def test_realistic_simulation():
    db = DatabaseManager("data/test_lot13.db")
    # Force high latency for test
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_latency_ms', '500')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_rejection_prob', '0.0')")
    
    portfolio = PortfolioEngine(db)
    risk = RiskEngine()
    class MockUniverse:
        def get_market_status(self, s): return "OPEN"
    
    exec_engine = ExecutionEngine(portfolio, db, risk, MockUniverse())
    
    signal = {"market_id": "BTC", "direction": "BUY", "sl": 90, "tp": 110}
    risk_data = {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0}
    ticker = {"last": 100}
    
    start_time = time.time()
    await exec_engine.execute_order("DEMO", signal, risk_data, ticker)
    duration = time.time() - start_time
    
    # Should be at least 0.5s due to simulated latency
    assert duration >= 0.5
