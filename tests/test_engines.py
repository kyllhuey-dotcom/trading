import pandas as pd
from api.engines.analysis_engine import AnalysisEngine
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine

# 1. TEST RISK ENGINE
def test_risk_position_sizing():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    # Case: 1000€ balance, entry 100, SL 95. Risk 1% (10€). Distance 5. Qty 2. Notional 200. Leverage 0.2.
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"]
    assert res["quantity"] == 2.0
    assert res["leverage"] == 0.2

def test_risk_leverage_cap():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=10)
    # Case: tight SL requiring high leverage. 1000€, entry 100, SL 99.9. Dist 0.1. Risk 10€. Qty 100. Notional 10000. Lev 10.
    # If SL was 99.95, Dist 0.05, Qty 200, Notional 20000, Lev 20. Should be capped at 10.
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=99.95)
    assert res["leverage"] <= 10.0
    assert res["risk_pct"] <= 1.0

# 2. TEST ANALYSIS ENGINE
def test_trend_detection():
    engine = AnalysisEngine(window=1)
    # Both highs and lows must increase for BULLISH
    highs = [10, 20, 15, 30, 25, 40, 35, 50]
    lows =  [5, 15, 10, 25, 20, 35, 30, 45]
    closes = [8, 18, 12, 28, 22, 38, 32, 48]
    data = {
        'High': highs,
        'Low':  lows,
        'Close': closes,
        'Timestamp': range(len(highs))
    }
    df = pd.DataFrame(data)
    res = engine.identify_structure(df)
    assert res["status"] == "VALID"
    assert res["trend"] == "BULLISH"

# 3. TEST SIGNAL ENGINE
def test_signal_block_on_range():
    signal = SignalEngine(min_score=75)
    analysis = {"market_state": "RANGE", "trend": "BULLISH", "status": "VALID", "last_low": 90, "last_high": 110}
    news = {"trading_allowed": True}
    # Provide enough rows and required columns for ATR calculation (Lot 1 fix)
    df = pd.DataFrame({
        'High': [110]*20,
        'Low': [90]*20,
        'Close': [100]*20, 
        'Volume': [10]*20
    })
    res = signal.generate_signal(analysis, news, df, strategy_mode="structure")
    assert res["status"] == "NO_TRADE"
    assert "Range" in res["reason"]
