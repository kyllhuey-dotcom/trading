import pytest
import pandas as pd
from api.engines.signal_engine import SignalEngine

def test_signal_engine_incomplete_data():
    """Rule: Lot 1 hardening check."""
    engine = SignalEngine(min_score=80)
    analysis = {"status": "VALID", "trend": "BULLISH"}
    
    # Missing columns
    df_missing = pd.DataFrame({"Close": [100]*20})
    res = engine.generate_signal(analysis, {"trading_allowed": True}, df_missing)
    assert res["status"] == "NO_TRADE"
    assert "Insufficient OHLCV data" in res["reason"]

    # Short data
    df_short = pd.DataFrame({
        "High": [110]*5, "Low": [90]*5, "Close": [100]*5
    })
    res = engine.generate_signal(analysis, {"trading_allowed": True}, df_short)
    assert res["status"] == "NO_TRADE"
    assert "Insufficient OHLCV data" in res["reason"]

def test_signal_engine_atr_nan():
    engine = SignalEngine(min_score=80)
    analysis = {"status": "VALID", "trend": "BULLISH", "last_low": 90, "last_high": 110}
    # Data with all NaNs
    df_nan = pd.DataFrame({
        "High": [float('nan')]*20, 
        "Low": [float('nan')]*20, 
        "Close": [float('nan')]*20
    })
    res = engine.generate_signal(analysis, {"trading_allowed": True}, df_nan)
    assert res["status"] == "NO_TRADE"
    assert "Insufficient valid data for ATR" in res["reason"]
