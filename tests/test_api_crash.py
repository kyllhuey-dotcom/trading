import os
os.environ["TESTING"] = "true"
from fastapi.testclient import TestClient
from api.index import app

client = TestClient(app)

def test_api_status_no_crash():
    """
    Test Rule 1 & 5: Ensure /api/status does not crash even if market data fails.
    This specifically tests the fix for UnboundLocalError: risk_reason.
    """
    response = client.get("/api/status?market_id=btc_usdt")
    # Even if there's a DATA ERROR (if internet is down in test env), 
    # it should return a 200 with status "DATA ERROR" or a valid StatusResponse
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "diagnosis" in data or data.get("status_display") == "DATA ERROR"

def test_risk_reason_defined_always():
    """
    Check if risk_reason is always handled in the response if diagnosis is present.
    """
    response = client.get("/api/status?market_id=btc_usdt")
    assert response.status_code == 200
    data = response.json()
    if "diagnosis" in data and data["diagnosis"]:
        assert "checks" in data["diagnosis"]
        assert "RISK_VALID" in data["diagnosis"]["checks"]
