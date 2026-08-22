import pytest
import os
os.environ["TESTING"] = "true"
from fastapi.testclient import TestClient
from api.index import app

client = TestClient(app)


def test_integration_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "OK"
    assert "uptime_s" in data


def test_integration_status_endpoint():
    response = client.get("/api/status?market_id=btc_usdt")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "selected_market" in data
    assert data["selected_market"] == "btc_usdt"
