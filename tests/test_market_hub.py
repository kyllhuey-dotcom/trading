from fastapi.testclient import TestClient
from api.engines.market_hub import (
    _real_sparkline,
    enrich_market_item,
    sort_hub_items,
)
from api.index import app

client = TestClient(app)


# --------------------------------------------------------------------------- #
# v3.3.2 (D1): synthetic_sparkline is REMOVED — the hub only carries REAL
# sparklines (latest 1h OHLCV closes from the scanner) or an empty list.     #
# --------------------------------------------------------------------------- #
def test_synthetic_sparkline_is_gone():
    import api.engines.market_hub as hub
    assert not hasattr(hub, "synthetic_sparkline"), (
        "synthetic_sparkline must stay removed: a fabricated sparkline is "
        "not real data")


def test_real_sparkline_keeps_only_real_closes():
    assert _real_sparkline([100.0, 101.5, 99.2]) == [100.0, 101.5, 99.2]
    # non-numeric and non-positive entries are dropped, nothing is invented
    assert _real_sparkline([100.0, "x", None, 0.0, -5, 101.0]) == [100.0, 101.0]
    # no input -> empty (the UI draws nothing), never a fabricated curve
    assert _real_sparkline(None) == []
    assert _real_sparkline([]) == []
    assert _real_sparkline("bogus") == []
    # capped to the latest 24 points
    assert _real_sparkline(list(range(1, 40)))[-1] == 39
    assert len(_real_sparkline(list(range(1, 40)))) == 24


def test_enrich_never_invents_a_sparkline():
    item = enrich_market_item(
        {"market_id": "btc_usdt", "display_symbol": "BTC/USDT",
         "name": "Bitcoin", "price": 10, "last": 10},
        {"btc_usdt": {"score": 88, "trend": "BULLISH", "strategy": "rsi",
                      "change": 1.2}})
    # no sparkline anywhere -> empty list, NOT a synthetic one
    assert item["sparkline"] == []
    # a REAL sparkline from the scan is kept verbatim
    real = [100.0, 100.5, 101.0]
    item2 = enrich_market_item(
        {"market_id": "btc_usdt", "last": 10},
        {"btc_usdt": {"sparkline": real, "sparkline_stale": False}})
    assert item2["sparkline"] == real
    assert item2["sparkline_stale"] is False
    # stale flag is propagated
    item3 = enrich_market_item(
        {"market_id": "btc_usdt", "last": 10},
        {"btc_usdt": {"sparkline": real, "sparkline_stale": True}})
    assert item3["sparkline_stale"] is True


# --------------------------------------------------------------------------- #
# v3.3.2 (D2): no fabricated price=0.0 — missing price stays None.           #
# --------------------------------------------------------------------------- #
def test_enrich_price_never_fabricated():
    # nothing at all -> None (UI renders "—"), not 0.0
    item = enrich_market_item(
        {"market_id": "x", "last": None},
        {"x": {"score": 0}})
    assert item["price"] is None
    assert item["change"] is None  # a 0.0 % change would be fake too
    # a real price is kept
    item2 = enrich_market_item(
        {"market_id": "x", "last": 123.45}, {})
    assert item2["price"] == 123.45
    # the scanner's price wins when the quote has none
    item3 = enrich_market_item(
        {"market_id": "x", "last": None},
        {"x": {"price": 77.0, "change": -1.5}})
    assert item3["price"] == 77.0
    assert item3["change"] == -1.5
    # a 0.0 "last" is no data, not a price
    item4 = enrich_market_item({"market_id": "x", "last": 0.0}, {})
    assert item4["price"] is None
    # a NEGATIVE change is real data and must be preserved
    item5 = enrich_market_item(
        {"market_id": "x", "last": 100.0, "change_24h": -2.5}, {})
    assert item5["price"] == 100.0
    assert item5["change"] == -2.5
    # a genuine 0.0 % change (flat day) is preserved too
    item6 = enrich_market_item(
        {"market_id": "x", "last": 100.0, "change_24h": 0.0}, {})
    assert item6["change"] == 0.0


def test_enrich_and_sort():
    item = enrich_market_item({"market_id": "btc_usdt", "display_symbol": "BTC/USDT", "name": "Bitcoin", "price": 10, "last": 10},
                              {"btc_usdt": {"score": 88, "trend": "BULLISH", "strategy": "rsi", "change": 1.2}})
    assert item["score"] == 88 and item["price"] == 10 and item["strategy"] == "rsi"
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
