import pytest
import pandas as pd
from api.engines.strategies.liquidity_gap import LiquidityGapStrategy

def test_liquidity_gap_buy():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    df = pd.DataFrame({'Close': [100]*20})
    
    # Gap in asks between level 0 and 1: (101 - 100.1)/100.1 = 0.89%
    orderbook = {
        'bids': [[100.0, 10], [99.9, 10]],
        'asks': [[100.1, 10], [101.0, 10]]
    }
    
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook)
    
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["score"] >= 60

def test_liquidity_gap_sell():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    df = pd.DataFrame({'Close': [100]*20})
    
    # Gap in bids: (99.9 - 99.0)/99.9 = 0.9%
    orderbook = {
        'bids': [[99.9, 10], [99.0, 10]],
        'asks': [[100.1, 10], [100.2, 10]]
    }
    
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook)
    
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "SELL"
    assert res["score"] >= 60

def test_liquidity_gap_none():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.5)
    df = pd.DataFrame({'Close': [100]*20})
    
    orderbook = {
        'bids': [[100.0, 10], [99.9, 10]],
        'asks': [[100.1, 10], [100.2, 10]]
    }
    
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook)
    assert res["status"] == "NO_TRADE"
