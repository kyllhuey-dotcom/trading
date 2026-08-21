import pytest
import asyncio
import pandas as pd
import numpy as np
import os
from datetime import datetime
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine
from api.engines.db_manager import DatabaseManager
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.analysis_engine import AnalysisEngine
from api.engines.strategies.tape_reading import TapeReadingStrategy
from api.engines.strategies.liquidity_gap import LiquidityGapStrategy
from api.index import tick_capital, bot_state

def clear_db(db_path="data/quantum_trade.db"):
    if os.path.exists(db_path):
        db = DatabaseManager(db_path)
        with db._get_connection() as conn:
            conn.execute("DELETE FROM trades")
            conn.commit()

# 1. RISK ENGINE
def test_risk_math_and_limits():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=10)
    res = risk.calculate_position_size(balance=10000.0, entry=100.0, stop_loss=90.0)
    assert res["allowed"] == True
    assert res["quantity"] == 10.0

# 2. SIGNAL ENGINE
def test_strategy_logic():
    signal = SignalEngine()
    df = pd.DataFrame({'High': [110]*50, 'Low': [90]*50, 'Close': [100]*50, 'Open': [100]*50, 'Volume': [1000]*50})
    cross_quotes = [{"last": 100.0}, {"last": 100.5}]
    res = signal.generate_signal({"market_id": "BTC", "status": "VALID"}, {}, df, strategy_mode="arbitrage", cross_quotes=cross_quotes)
    assert res["status"] == "SIGNAL_DETECTED"

# 3. TAPE READING
def test_tape_reading():
    strategy = TapeReadingStrategy()
    df = pd.DataFrame({'Close': [100]*20, 'Volume': [1000]*20})
    res = strategy.generate_signal("BTC", df, orderbook={'bids': [[100.1, 1000]], 'asks': [[100.2, 100]]}, trades=[{'side': 'buy', 'amount': 1000}])
    assert res["status"] == "SIGNAL_DETECTED"

# 4. ANALYSIS ENGINE (Zig Zag Data)
def test_analysis_structure():
    engine = AnalysisEngine(window=1)
    # Higher Highs and Higher Lows
    highs = [100, 110, 105, 115, 110, 120]
    lows =  [90, 100, 95, 105, 100, 110]
    closes = [95, 105, 100, 110, 105, 115]
    df = pd.DataFrame({'High': highs, 'Low': lows, 'Close': closes, 'Timestamp': range(len(highs))})
    res = engine.identify_structure(df)
    assert res["status"] == "VALID"
    assert res["trend"] == "BULLISH"

# 5. MICRO-LOOP TICK
@pytest.mark.asyncio
async def test_tick_capital_sync():
    clear_db()
    bot_state["mode"] = "DEMO"
    from api.index import portfolio_engine
    portfolio_engine.set_balance("DEMO", 10000.0)
    await tick_capital()
    assert bot_state["equity"] == 10000.0

