import pytest
from api.engines.risk_engine import RiskEngine

def test_risk_sl_side_validation():
    re = RiskEngine(max_risk_pct=1.0)
    
    # BUY: SL must be < Entry
    res = re.calculate_position_size(balance=1000, entry=100, stop_loss=105, direction="BUY")
    assert res["allowed"] is False
    assert "Invalid SL for BUY" in res["reason"]
    
    # SELL: SL must be > Entry
    res = re.calculate_position_size(balance=1000, entry=100, stop_loss=95, direction="SELL")
    assert res["allowed"] is False
    assert "Invalid SL for SELL" in res["reason"]

def test_risk_daily_loss_limit():
    re = RiskEngine(max_daily_loss_pct=3.0)
    re.daily_pnl = -35.0 # Loss of 3.5% on 1000
    res = re.calculate_position_size(balance=1000, entry=100, stop_loss=95)
    assert res["allowed"] is False
    assert "Max Daily Loss reached" in res["reason"]
