from fastapi.testclient import TestClient
from api.engines.market_hub import synthetic_sparkline, enrich_market_item, sort_hub_items
from api.index import app

client = TestClient(app)


def test_sparkline_deterministic():
    a = synthetic_sparkline(100, 10)
    b = synthetic_sparkline(100, 10)
    assert a == b and a[-1] == 100 and len(a) == 12


def test_enrich_and_sort():
    item = enrich_market_item({"market_id": "btc_usdt", "display_symbol": "BTC/USDT", "name": "Bitcoin", "price": 10, "last": 10},
                              {"btc_usdt": {"score": 88, "trend": "BULLISH", "strategy": "tape", "change": 1.2}})
    assert item["score"] == 88 and item["sparkline"] and item["strategy"] == "tape"
    items = [{"score": 1, "volume": 9, "symbol": "a"}, {"score": 9, "volume": 1, "symbol": "b"}]
    assert sort_hub_items(items, "score", True)[0]["score"] == 9
    assert sort_hub_items(items, "volume", True)[0]["volume"] == 9


def test_markets_api_additive(monkeypatch):
    # The overview endpoint must not hit live providers in unit tests.
    async def _fake_overview():
        return {
            "CRYPTO": [{"market_id": "btc_usdt", "display_symbol": "BTC/USDT",
                        "name": "Bitcoin", "last": 100.0, "score": 90}],
            "FOREX": [{"market_id": "eurusd", "display_symbol": "EUR/USD",
                       "name": "Euro", "last": 1.08, "score": 50}],
            "STOCKS": [{"market_id": "aapl", "display_symbol": "AAPL",
                        "name": "Apple", "last": 200.0, "score": 70}],
        }

    import api.index as idx
    monkeypatch.setattr(idx.data_engine, "get_market_overview", _fake_overview)
    # Ensure no previous scan interferes with enrichment.
    monkeypatch.setitem(idx.bot_state, "latest_scan", [])

    r = client.get("/api/markets?sort=score&order=desc")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    assert any(k in data for k in ("CRYPTO", "FOREX", "STOCKS"))


def test_html_hub():
    html = open("public/index.html", encoding="utf-8").read()
    assert "data-hub-sort" in html and "hub-hot" in html
