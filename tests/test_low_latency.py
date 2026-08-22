from api.engines.provider_priority import prioritize_providers, PRIORITY
from api.engines.data_engine import DataEngine


def test_priority_order():
    items = prioritize_providers([("yahoo_forex", "X"), ("gate", "G"), ("binance", "B"), ("bybit", "Y")])
    assert [p for p, _ in items] == ["binance", "bybit", "gate", "yahoo_forex"]
    assert PRIORITY["binance"] < PRIORITY["bybit"] < PRIORITY["gate"]


def test_scalping_guards():
    de = DataEngine()
    assert de.check_scalping_allowed("eur_usd")["allowed"] is False
    assert de.check_scalping_allowed("btc_usdt")["allowed"] is True


def test_binance_change_24h_source():
    src = open("api/engines/data_providers/binance_provider.py", encoding="utf-8").read()
    assert "change_24h=ticker.get('percentage')" in src
