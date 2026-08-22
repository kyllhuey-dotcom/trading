import pandas as pd
from api.engines.strategies.tape_reading import TapeReadingStrategy

def test_tape_reading_buy_signal():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    df = pd.DataFrame({
        'Close': [100, 100.1, 100.2, 100.3, 100.5],
        'Volume': [10, 10, 10, 10, 20] # Volume spike
    })
    
    # Imbalance 50% (Buy 15, Sell 5)
    orderbook = {
        'bids': [[100.4, 15]],
        'asks': [[100.6, 5]]
    }
    
    # Delta 100% (All buys)
    trades = [
        {'side': 'buy', 'amount': 10},
        {'side': 'buy', 'amount': 10}
    ]
    
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook, trades=trades)
    
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["score"] >= 70

def test_tape_reading_sell_signal():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    df = pd.DataFrame({
        'Close': [100.5, 100.4, 100.3, 100.2, 100.0],
        'Volume': [10, 10, 10, 10, 20]
    })
    
    # Imbalance -60% (Buy 4, Sell 16)
    orderbook = {
        'bids': [[99.9, 4]],
        'asks': [[100.1, 16]]
    }
    
    # Delta -100% (All sells)
    trades = [
        {'side': 'sell', 'amount': 10},
        {'side': 'sell', 'amount': 10}
    ]
    
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook, trades=trades)
    
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "SELL"
