"""
LOT C — Robustesse tape reading.

Covers:
- depth-weighted orderbook imbalance (nearby levels weigh more);
- proportional signed price velocity (+ volume spike bonus);
- dynamic volatility-driven pressure threshold (ATR-based, clamped);
- conviction multiplier (aligned vs conflicting flow components);
- full signal generation with mocked orderbook / trades / OHLCV.
"""
import pandas as pd
import pytest

from api.engines.signal_engine import SignalEngine
from api.engines.strategies.tape_reading import TapeReadingStrategy


def _flat_df(close: float = 100.0, n: int = 20, volume: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({'Close': [close] * n, 'Volume': [volume] * n})


def _ohlcv_df(bar_range: float = 0.05, close: float = 100.4,
              last_volume: float = 200.0, base_volume: float = 100.0) -> pd.DataFrame:
    """20 bars, small range → controllable ATR; last bar = volume spike + uptick."""
    closes = [100.0] * 19 + [close]
    highs = [c + bar_range for c in closes]
    lows = [c - bar_range for c in closes]
    volumes = [base_volume] * 19 + [last_volume]
    return pd.DataFrame({'Close': closes, 'High': highs, 'Low': lows, 'Volume': volumes})


# --------------------------------------------------------------------------- #
# 1. Depth-weighted orderbook imbalance                                       #
# --------------------------------------------------------------------------- #
def test_weighted_imbalance_favors_nearby_levels():
    strategy = TapeReadingStrategy()
    # Raw sums are equal (1010 vs 1010) but big bid volume sits far away:
    orderbook = {
        'bids': [[100.0, 10.0], [99.0, 1000.0]],
        'asks': [[101.0, 1000.0], [102.0, 10.0]],
    }
    weighted = strategy._weighted_imbalance(orderbook)
    # Near levels: bids 10 vs asks 1000 → negative imbalance despite raw equality
    assert weighted < 0
    # Sanity: symmetric book → 0
    assert strategy._weighted_imbalance(
        {'bids': [[100.0, 5.0]], 'asks': [[101.0, 5.0]]}) == pytest.approx(0.0)


def test_weighted_imbalance_empty_inputs():
    strategy = TapeReadingStrategy()
    assert strategy._weighted_imbalance(None) == 0.0
    assert strategy._weighted_imbalance({'bids': [], 'asks': []}) == 0.0


# --------------------------------------------------------------------------- #
# 2. Proportional price velocity                                              #
# --------------------------------------------------------------------------- #
def test_velocity_proportional_to_price_move():
    strategy = TapeReadingStrategy()
    up = pd.DataFrame({'Close': [100.0, 100.0, 100.0, 100.0, 101.0]})  # +1%
    assert strategy._price_velocity(up) == pytest.approx(15.0, abs=0.01)

    down = pd.DataFrame({'Close': [101.0, 101.0, 101.0, 101.0, 100.0]})  # -0.99%
    assert strategy._price_velocity(down) == pytest.approx(-14.85, abs=0.05)


def test_velocity_volume_spike_bonus():
    strategy = TapeReadingStrategy()
    spike = pd.DataFrame({'Close': [100.0] * 4 + [100.5], 'Volume': [10.0] * 4 + [40.0]})
    # +0.5% * 15 = 7.5, spike bonus +10
    assert strategy._price_velocity(spike) == pytest.approx(17.5, abs=0.01)

    no_spike = pd.DataFrame({'Close': [100.0] * 4 + [100.5], 'Volume': [10.0] * 5})
    assert strategy._price_velocity(no_spike) == pytest.approx(7.5, abs=0.01)


def test_velocity_clamped_and_missing_data():
    strategy = TapeReadingStrategy()
    wild = pd.DataFrame({'Close': [100.0, 100.0, 100.0, 100.0, 150.0]})  # +50%
    assert strategy._price_velocity(wild) == pytest.approx(30.0)  # base clamp
    wild_spike = pd.DataFrame({'Close': [100.0] * 4 + [150.0], 'Volume': [100.0] * 4 + [400.0]})
    assert strategy._price_velocity(wild_spike) == pytest.approx(40.0)  # spike bonus to the max
    assert strategy._price_velocity(pd.DataFrame({'Close': [100.0]})) == 0.0
    assert strategy._price_velocity(None) == 0.0


# --------------------------------------------------------------------------- #
# 3. Dynamic volatility-driven threshold                                      #
# --------------------------------------------------------------------------- #
def test_low_volatility_lowers_threshold():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    df = _ohlcv_df(bar_range=0.05)  # ATR ≈ 0.1 → atr_pct ≈ 0.1%
    atr_pct = strategy._atr_pct(df)
    assert atr_pct is not None and atr_pct < 0.15
    threshold = strategy._dynamic_threshold(atr_pct)
    assert threshold < strategy.pressure_threshold
    assert threshold >= strategy.min_pressure_threshold


def test_high_volatility_raises_threshold():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    df = _ohlcv_df(bar_range=1.0)  # ATR ≈ 2.0 → atr_pct ≈ 2%
    atr_pct = strategy._atr_pct(df)
    assert atr_pct is not None and atr_pct > 1.5
    threshold = strategy._dynamic_threshold(atr_pct)
    assert threshold == strategy.max_pressure_threshold  # clamped at 60


def test_threshold_falls_back_without_ohlcv():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    assert strategy._atr_pct(_flat_df()) is None  # no High/Low
    assert strategy._dynamic_threshold(None) == 40.0


def test_dynamic_threshold_can_be_disabled():
    strategy = TapeReadingStrategy(pressure_threshold=40.0, dynamic_threshold=False)
    df = _ohlcv_df(bar_range=1.0)  # high vol would normally raise the threshold
    atr_pct = strategy._atr_pct(df)
    assert atr_pct is not None
    assert strategy._dynamic_threshold(atr_pct) == 40.0


# --------------------------------------------------------------------------- #
# 4. End-to-end: same tape flips decision with volatility                     #
# --------------------------------------------------------------------------- #
def _tape() -> tuple:
    orderbook = {'bids': [[100.3, 60.0]], 'asks': [[100.5, 40.0]]}  # +20 imbalance
    trades = [{'side': 'buy', 'amount': 1.0}] * 3 + [{'side': 'sell', 'amount': 1.0}]  # +50 delta
    return orderbook, trades


def test_same_tape_signals_in_calm_market_only():
    orderbook, trades = _tape()
    calm = TapeReadingStrategy(pressure_threshold=40.0)
    wild = TapeReadingStrategy(pressure_threshold=40.0)

    res_calm = calm.generate_signal("btc_usdt", _ohlcv_df(bar_range=0.05),
                                    orderbook=orderbook, trades=trades)
    assert res_calm["status"] == "SIGNAL_DETECTED"
    assert res_calm["direction"] == "BUY"
    assert res_calm["metadata"]["threshold"] < 40.0
    assert res_calm["metadata"]["atr_pct"] is not None

    res_wild = wild.generate_signal("btc_usdt", _ohlcv_df(bar_range=1.0),
                                    orderbook=orderbook, trades=trades)
    assert res_wild["status"] == "NO_TRADE"
    assert res_wild["metadata"]["threshold"] == 60.0  # clamped for wild market
    assert "threshold" in res_wild["reason"]


# --------------------------------------------------------------------------- #
# 5. Conviction multiplier                                                    #
# --------------------------------------------------------------------------- #
def test_conflicting_components_dampen_pressure():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    orderbook = {'bids': [[100.0, 9.0]], 'asks': [[101.0, 1.0]]}          # +80
    trades = [{'side': 'buy', 'amount': 3.0}, {'side': 'sell', 'amount': 7.0}]  # -40
    # +2.667% move → base velocity +30, volume spike bonus +10 → +40
    df = pd.DataFrame({'Close': [100.0] * 4 + [102.667], 'Volume': [100.0] * 4 + [400.0]})
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook, trades=trades)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"  # net pressure stays positive
    assert res["metadata"]["conviction"] == 0.85


def test_aligned_components_reinforce_pressure():
    strategy = TapeReadingStrategy(pressure_threshold=40.0)
    orderbook = {'bids': [[100.0, 9.0]], 'asks': [[101.0, 1.0]]}          # +80
    trades = [{'side': 'buy', 'amount': 9.0}, {'side': 'sell', 'amount': 1.0}]  # +80
    df = pd.DataFrame({'Close': [100.0] * 4 + [102.667]})                  # +30 velocity
    res = strategy.generate_signal("btc_usdt", df, orderbook=orderbook, trades=trades)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["metadata"]["conviction"] == 1.15
    assert res["metadata"]["pressure_score"] > 100


# --------------------------------------------------------------------------- #
# 6. No price reference → defensive NO_TRADE                                  #
# --------------------------------------------------------------------------- #
def test_no_price_reference_is_blocked():
    strategy = TapeReadingStrategy(pressure_threshold=10.0)
    orderbook = {'bids': [[100.0, 9.0]], 'asks': [[101.0, 1.0]]}
    res = strategy.generate_signal("btc_usdt", pd.DataFrame(),
                                   orderbook=orderbook, trades=None)
    assert res["status"] == "NO_TRADE"


# --------------------------------------------------------------------------- #
# 7. SignalEngine pass-through (tape mode)                                    #
# --------------------------------------------------------------------------- #
def test_signal_engine_tape_mode_passes_flow_data():
    engine = SignalEngine(min_score=50)
    # v2.8: strong pressure so the tape score clears the 84 floor
    orderbook = {'bids': [[100.3, 75.0]], 'asks': [[100.5, 25.0]]}
    trades = [{'side': 'buy', 'amount': 1.0}] * 4
    df = _ohlcv_df(bar_range=0.05)
    res = engine.generate_signal({"status": "VALID", "market_id": "btc_usdt"},
                                 {"trading_allowed": True}, df,
                                 strategy_mode="tape", market_id="btc_usdt",
                                 orderbook=orderbook, trades=trades)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["strategy"] == "tape"
    assert res["market_id"] == "btc_usdt"
    assert "imbalance" in res.get("metadata", {})
