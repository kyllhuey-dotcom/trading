import pandas as pd
from api.engines.strategies.micro_arbitrage import MicroArbitrageStrategy

def test_arbitrage_buy_signal():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    df = pd.DataFrame({'Close': [100]*20})
    
    # Primary is 100, others are 100.2 and 100.3. Avg is 100.166. Primary is cheaper.
    # Spread (100.3 - 100) / 100 = 0.3%. Threshold is 0.15%.
    cross_quotes = [
        {"last": 100.0, "provider": "gate"},
        {"last": 100.2, "provider": "bybit"},
        {"last": 100.3, "provider": "binance"}
    ]
    
    res = strategy.generate_signal("btc_usdt", df, cross_quotes=cross_quotes)
    
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["score"] >= 80
    assert "Arbitrage" in res["reason"]

def test_arbitrage_sell_signal():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    df = pd.DataFrame({'Close': [100]*20})
    
    # Primary is 100.3, others are 100.0 and 100.1. Primary is more expensive.
    cross_quotes = [
        {"last": 100.3, "provider": "gate"},
        {"last": 100.0, "provider": "bybit"},
        {"last": 100.1, "provider": "binance"}
    ]
    
    res = strategy.generate_signal("btc_usdt", df, cross_quotes=cross_quotes)
    
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "SELL"
    assert res["score"] >= 80

def test_arbitrage_no_signal_low_spread():
    strategy = MicroArbitrageStrategy(threshold_pct=0.15)
    df = pd.DataFrame({'Close': [100]*20})
    
    # Spread (100.1 - 100) / 100 = 0.1% < 0.15%
    cross_quotes = [
        {"last": 100.0, "provider": "gate"},
        {"last": 100.1, "provider": "bybit"}
    ]
    
    res = strategy.generate_signal("btc_usdt", df, cross_quotes=cross_quotes)
    
    assert res["status"] == "NO_TRADE"
    assert "Spread too low" in res["reason"]

def test_arbitrage_insufficient_data():
    strategy = MicroArbitrageStrategy()
    df = pd.DataFrame({'Close': [100]*20})
    
    assert strategy.generate_signal("btc_usdt", df, cross_quotes=[])["status"] == "NO_TRADE"
    assert strategy.generate_signal("btc_usdt", df, cross_quotes=[{"last": 100.0}])["status"] == "NO_TRADE"
