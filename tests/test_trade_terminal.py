from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from api.engines.order_types import should_fill_now, risk_based_quantity, normalize_order_type
from api.engines.execution_engine import ExecutionEngine
from api.index import app, data_engine

client = TestClient(app)


def test_fill_rules_limit_stop():
    assert should_fill_now("LIMIT", "BUY", 99, limit_price=100) is True
    assert should_fill_now("LIMIT", "BUY", 101, limit_price=100) is False
    assert should_fill_now("LIMIT", "SELL", 101, limit_price=100) is True
    assert should_fill_now("STOP", "BUY", 101, stop_price=100) is True
    assert should_fill_now("STOP", "SELL", 99, stop_price=100) is True
    assert normalize_order_type("limit") == "LIMIT"


def test_risk_based_quantity():
    q = risk_based_quantity(10_000, 1.0, 100, 99)
    assert abs(q - 100.0) < 1e-9


async def test_limit_queued_then_fills(tmp_path, monkeypatch):
    from api.engines.db_manager import DatabaseManager
    from api.engines.portfolio_engine import PortfolioEngine
    from api.engines.risk_engine import RiskEngine
    from api.engines.market_universe import MarketUniverse
    db = DatabaseManager(str(tmp_path / "t.db"))
    db.set_setting("sim_rejection_prob", "0")
    db.set_setting("sim_latency_ms", "0")
    port = PortfolioEngine(db_manager=db)
    eng = ExecutionEngine(port, db, RiskEngine(), MarketUniverse())
    sig = {"market_id": "btc_usdt", "direction": "BUY", "entry": 100, "sl": 90, "tp": 120,
           "order_type": "LIMIT", "limit_price": 95, "display_symbol": "BTC/USDT"}
    risk = {"quantity": 0.01, "leverage": 1, "estimated_fees": 0.1}
    res = await eng.execute_order("DEMO", sig, risk, {"last": 100, "ask": 100, "bid": 99})
    assert res.get("pending") is True
    filled = await eng.process_pending_orders("DEMO", {"btc_usdt": {"last": 94, "ask": 94, "bid": 93}})
    assert filled and filled[0].get("success")


def test_orderbook_ohlcv_mocked():
    data_engine.fetch_order_book = AsyncMock(return_value={"bids": [[1, 1]], "asks": [[2, 1]]})
    data_engine.fetch_ticker = AsyncMock(return_value={"last": 1, "timestamp": 1})
    r = client.get("/api/orderbook?market_id=btc_usdt")
    assert r.status_code == 200
    assert r.json()["market_id"] == "btc_usdt" and "bids" in r.json()

    import pandas as pd
    df = pd.DataFrame({"Timestamp": [1, 2], "Open": [1, 1], "High": [2, 2], "Low": [1, 1], "Close": [1.5, 1.6], "Volume": [1, 1]})
    data_engine.fetch_ohlcv = AsyncMock(return_value=df)
    r = client.get("/api/ohlcv?market_id=btc_usdt&timeframe=1m&limit=60")
    assert r.status_code == 200
    assert "candles" in r.json()


def test_unknown_market_order_400():
    r = client.post("/api/order", json={"market_id": "__nope__", "direction": "BUY", "quantity": 1})
    assert r.status_code == 400


def test_html_terminal():
    html = open("public/index.html", encoding="utf-8").read()
    for t in ("data-otype", "execute-signal-btn", "orderbook-panel", "loadTerminalChart"):
        assert t in html
