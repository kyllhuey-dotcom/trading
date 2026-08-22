import pytest
import asyncio
import pandas as pd
import numpy as np
import os
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.analysis_engine import AnalysisEngine
from api.engines.strategies.tape_reading import TapeReadingStrategy
from api.engines.strategies.liquidity_gap import LiquidityGapStrategy
from api.index import tick_capital, bot_state


def _clear_open_positions():
    """Safely reset DEMO state without touching historical data."""
    from api.index import demo_execution, portfolio_engine
    demo_execution.clear_active_positions("DEMO")
    portfolio_engine.set_balance("DEMO", 10000.0)
    from api.index import risk_engine
    risk_engine.daily_pnl = 0.0
    risk_engine.last_loss_time = None


# 1. RISK ENGINE
def test_risk_math_and_limits():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=10)
    res = risk.calculate_position_size(balance=10000.0, entry=100.0, stop_loss=90.0)
    assert res["allowed"] == True
    assert res["quantity"] == 10.0


# 2. SIGNAL ENGINE
def test_strategy_logic():
    signal = SignalEngine()
    df = pd.DataFrame({'High': [110]*50, 'Low': [90]*50, 'Close': [100]*50,
                       'Open': [100]*50, 'Volume': [1000]*50})
    cross_quotes = [{"last": 100.0}, {"last": 100.5}]
    res = signal.generate_signal({"market_id": "BTC", "status": "VALID"}, {}, df,
                                 strategy_mode="arbitrage", cross_quotes=cross_quotes)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["market_id"] == "BTC"


# 3. TAPE READING
def test_tape_reading():
    strategy = TapeReadingStrategy()
    df = pd.DataFrame({'Close': [100]*20, 'Volume': [1000]*20})
    res = strategy.generate_signal("BTC", df,
                                   orderbook={'bids': [[100.1, 1000]], 'asks': [[100.2, 100]]},
                                   trades=[{'side': 'buy', 'amount': 1000}])
    assert res["status"] == "SIGNAL_DETECTED"


# 4. ANALYSIS ENGINE (Zig Zag Data)
def test_analysis_structure():
    engine = AnalysisEngine(window=1)
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
    _clear_open_positions()
    bot_state["mode"] = "DEMO"
    await tick_capital()
    assert bot_state["equity"] == 10000.0
    assert bot_state["balance"] == 10000.0
