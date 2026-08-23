from fastapi.testclient import TestClient
from api.engines.radar import sort_assets, filter_assets, enrich_radar_row, format_data_age
from api.index import app, bot_state

client = TestClient(app)

SAMPLE = [
    {"symbol": "eth_usdt", "asset_class": "CRYPTO", "score": 70, "signal_data": {"strategy": "tape", "direction": "SELL", "display_symbol": "ETH/USDT"}},
    {"symbol": "btc_usdt", "asset_class": "CRYPTO", "score": 92, "signal_data": {"strategy": "structure", "direction": "BUY", "display_symbol": "BTC/USDT"}},
    {"symbol": "eur_usd", "asset_class": "FOREX", "score": 85, "signal_data": {"strategy": "structure", "direction": "BUY"}},
]


def test_sort_desc_default_and_asc():
    desc = sort_assets(SAMPLE, "score", True)
    assert [a["symbol"] for a in desc] == ["btc_usdt", "eur_usd", "eth_usdt"]
    asc = sort_assets(SAMPLE, "score", False)
    assert [a["symbol"] for a in asc] == ["eth_usdt", "eur_usd", "btc_usdt"]


def test_filters_ge80_ge90_crypto():
    assert [a["symbol"] for a in filter_assets(SAMPLE, "ge80")] == ["btc_usdt", "eur_usd"]
    assert [a["symbol"] for a in filter_assets(SAMPLE, "ge90")] == ["btc_usdt"]
    assert all(a["asset_class"] == "CRYPTO" for a in filter_assets(SAMPLE, "crypto"))


def test_enrich_strategy_display():
    row = enrich_radar_row(SAMPLE[0])
    assert row["strategy"] == "tape"
    assert row["display_symbol"] == "ETH/USDT"
    assert "data_age_label" in row
    assert format_data_age(250) == "250ms"


def test_scanner_contract_additive_and_order():
    bot_state["latest_scan"] = SAMPLE
    r = client.get("/api/scanner?sort=score&order=desc&filter=all")
    assert r.status_code == 200
    data = r.json()
    assert "assets" in data and "duration_s" in data
    assert data["sort"] == "score" and data["order"] == "desc" and data["filter"] == "all"
    scores = [a["score"] for a in data["assets"]]
    assert scores == sorted(scores, reverse=True)


def test_execute_signal_validation():
    r = client.post("/api/execute-signal", json={})
    assert r.status_code == 400
    r = client.post("/api/execute-signal", json={"market_id": "__nope__"})
    assert r.status_code == 400
    bot_state["latest_scan"] = [{"symbol": "btc_usdt", "score": 40, "signal_data": {"status": "SIGNAL_DETECTED", "entry": 1, "sl": 0.9, "score": 40, "market_id": "btc_usdt"}}]
    r = client.post("/api/execute-signal", json={"market_id": "btc_usdt"})
    assert r.status_code == 200
    assert r.json().get("success") is False


def test_html_radar_hooks():
    html = open("public/index.html", encoding="utf-8").read()
    for token in ("data-radar-filter", "toggleRadarSort", "executeRadarTrade", "Prix live", "Stratégie", "Age data"):
        assert token in html
