import pytest
from api.engines.risk_engine import RiskEngine

def test_risk_uninitialized_vars():
    """
    Test Rule 5: Ensure RiskEngine always returns structured data.
    """
    re = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    # Scenario: Very low balance
    res = re.calculate_position_size(balance=5.0, entry=50000.0, stop_loss=49000.0)
    assert res["allowed"] is False
    assert "reason" in res

    # Scenario: Normal balance but 0 SL distance
    res = re.calculate_position_size(balance=1000.0, entry=50000.0, stop_loss=50000.0)
    assert res["allowed"] is False
    assert "reason" in res

def test_risk_drawdown_limit():
    re = RiskEngine(max_risk_pct=1.0, max_drawdown_pct=5.0)
    re.peak_balance = 10000.0
    # 9000 is 10% drawdown
    res = re.calculate_position_size(balance=9000.0, entry=50000.0, stop_loss=49000.0)
    assert res["allowed"] is False
    assert "Max Drawdown reached" in res["reason"]
